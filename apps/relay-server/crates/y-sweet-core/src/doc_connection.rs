use crate::api_types::Authorization;
use crate::sync::{
    self, awareness::Awareness, DefaultProtocol, EventMessage, Message, Protocol, SyncMessage,
    MAX_QUERY_SUBDOCS_PER_REQUEST, MSG_SYNC, MSG_SYNC_UPDATE,
};
use crate::sync_kv::SyncKv;
use std::collections::HashSet;
use std::sync::{Arc, OnceLock, RwLock};
use yrs::{
    block::ClientID,
    encoding::write::Write,
    updates::{
        decoder::Decode,
        encoder::{Encode, Encoder, EncoderV1},
    },
    Array, Map, Out, ReadTxn, Subscription, Transact, Update,
};

fn current_time_epoch_millis() -> u64 {
    let now = std::time::SystemTime::now();
    let duration_since_epoch = now.duration_since(std::time::UNIX_EPOCH).unwrap();
    duration_since_epoch.as_millis() as u64
}

/// Granularity of the `last_query` stamp in the subdoc index (one UTC day).
const MS_PER_DAY: u64 = 86_400_000;

// TODO: this is an implementation detail and should not be exposed.
pub const DOC_NAME: &str = "doc";

#[cfg(not(feature = "sync"))]
type Callback = Arc<dyn Fn(&[u8]) + 'static>;

#[cfg(feature = "sync")]
type Callback = Arc<dyn Fn(&[u8]) + 'static + Send + Sync>;

const SYNC_STATUS_MESSAGE: u8 = 102;

pub struct DocConnection {
    awareness: Arc<RwLock<Awareness>>,
    #[allow(unused)] // acts as RAII guard
    doc_subscription: Subscription,
    #[allow(unused)] // acts as RAII guard
    awareness_subscription: Subscription,
    authorization: Authorization,
    callback: Callback,
    closed: Arc<OnceLock<()>>,

    /// If the client sends an awareness state, this will be set to its client ID.
    /// It is used to clear the awareness state when a client disconnects.
    client_id: OnceLock<ClientID>,

    /// Event types that this connection is subscribed to
    event_subscriptions: Arc<RwLock<HashSet<String>>>,

    /// Expiration time for the authentication token in milliseconds since epoch.
    /// If None, the token never expires.
    expiration_time: Option<u64>,

    /// Optional reference to the document's SyncKv for reading subdoc snapshots
    sync_kv: Option<Arc<SyncKv>>,

    /// Authenticated user identity from the connection token.
    /// When set, the server will register new client_ids under this user in the "users" map.
    user: Option<String>,
}

impl DocConnection {
    #[cfg(not(feature = "sync"))]
    pub fn new<F>(
        awareness: Arc<RwLock<Awareness>>,
        authorization: Authorization,
        callback: F,
    ) -> Self
    where
        F: Fn(&[u8]) + 'static,
    {
        Self::new_inner(awareness, authorization, None, Arc::new(callback))
    }

    #[cfg(feature = "sync")]
    pub fn new<F>(
        awareness: Arc<RwLock<Awareness>>,
        authorization: Authorization,
        callback: F,
    ) -> Self
    where
        F: Fn(&[u8]) + 'static + Send + Sync,
    {
        Self::new_inner(awareness, authorization, None, Arc::new(callback))
    }

    #[cfg(not(feature = "sync"))]
    pub fn new_with_expiration<F>(
        awareness: Arc<RwLock<Awareness>>,
        authorization: Authorization,
        expiration_time: Option<u64>,
        callback: F,
    ) -> Self
    where
        F: Fn(&[u8]) + 'static,
    {
        Self::new_inner(
            awareness,
            authorization,
            expiration_time,
            Arc::new(callback),
        )
    }

    #[cfg(feature = "sync")]
    pub fn new_with_expiration<F>(
        awareness: Arc<RwLock<Awareness>>,
        authorization: Authorization,
        expiration_time: Option<u64>,
        callback: F,
    ) -> Self
    where
        F: Fn(&[u8]) + 'static + Send + Sync,
    {
        Self::new_inner(
            awareness,
            authorization,
            expiration_time,
            Arc::new(callback),
        )
    }

    pub fn new_inner(
        awareness: Arc<RwLock<Awareness>>,
        authorization: Authorization,
        expiration_time: Option<u64>,
        callback: Callback,
    ) -> Self {
        let closed = Arc::new(OnceLock::new());

        let (doc_subscription, awareness_subscription) = {
            let mut awareness = awareness.write().unwrap();

            // Initial handshake is based on this:
            // https://github.com/y-crdt/y-sync/blob/56958e83acfd1f3c09f5dd67cf23c9c72f000707/src/sync.rs#L45-L54

            {
                // Send a server-side state vector, so that the client can send
                // updates that happened offline.
                let sv = awareness.doc().transact().state_vector();
                let sync_step_1 = Message::Sync(SyncMessage::SyncStep1(sv)).encode_v1();
                callback(&sync_step_1);
            }

            {
                // Send the initial awareness state.
                let update = awareness.update().unwrap();
                let awareness = Message::Awareness(update).encode_v1();
                callback(&awareness);
            }

            let doc_subscription = {
                let doc = awareness.doc();
                let callback = callback.clone();
                let closed = closed.clone();
                doc.observe_update_v1(move |_, event| {
                    if closed.get().is_some() || expired_at(expiration_time) {
                        return;
                    }
                    // https://github.com/y-crdt/y-sync/blob/56958e83acfd1f3c09f5dd67cf23c9c72f000707/src/net/broadcast.rs#L47-L52
                    let mut encoder = EncoderV1::new();
                    encoder.write_var(MSG_SYNC);
                    encoder.write_var(MSG_SYNC_UPDATE);
                    encoder.write_buf(&event.update);
                    let msg = encoder.to_vec();
                    callback(&msg);
                })
                .unwrap()
            };

            let callback = callback.clone();
            let closed = closed.clone();
            let awareness_subscription = awareness.on_update(move |awareness, e| {
                if closed.get().is_some() || expired_at(expiration_time) {
                    return;
                }

                // https://github.com/y-crdt/y-sync/blob/56958e83acfd1f3c09f5dd67cf23c9c72f000707/src/net/broadcast.rs#L59
                let added = e.added();
                let updated = e.updated();
                let removed = e.removed();
                let mut changed = Vec::with_capacity(added.len() + updated.len() + removed.len());
                changed.extend_from_slice(added);
                changed.extend_from_slice(updated);
                changed.extend_from_slice(removed);

                if let Ok(u) = awareness.update_with_clients(changed) {
                    let msg = Message::Awareness(u).encode_v1();
                    callback(&msg);
                }
            });

            (doc_subscription, awareness_subscription)
        };

        Self {
            awareness,
            doc_subscription,
            awareness_subscription,
            authorization,
            callback,
            client_id: OnceLock::new(),
            closed,
            event_subscriptions: Arc::new(RwLock::new(HashSet::new())),
            expiration_time,
            sync_kv: None,
            user: None,
        }
    }

    /// Set the SyncKv reference for subdoc snapshot queries
    pub fn set_sync_kv(&mut self, sync_kv: Arc<SyncKv>) {
        self.sync_kv = Some(sync_kv);
    }

    /// Set the authenticated user identity for server-driven PUD registration.
    pub fn set_user(&mut self, user: String) {
        self.user = Some(user);
    }

    /// Snapshot the current state vector's client_ids (for before/after comparison).
    fn snapshot_sv(&self, awareness: &Awareness) -> std::collections::HashSet<ClientID> {
        let txn = awareness.doc().transact();
        txn.state_vector()
            .iter()
            .map(|(&cid, &_clock)| cid)
            .collect()
    }

    /// After applying an update, register any client_ids that are new in the state vector.
    /// The caller must already hold the awareness lock — pass the guard directly.
    fn register_new_client_ids(
        &self,
        awareness: &Awareness,
        sv_before: &std::collections::HashSet<ClientID>,
    ) {
        let user_id = match &self.user {
            Some(u) => u,
            None => return,
        };

        let sv_after = awareness.doc().transact().state_vector();

        let new_ids: Vec<ClientID> = sv_after
            .iter()
            .filter_map(|(&cid, &_clock)| {
                if !sv_before.contains(&cid) {
                    Some(cid)
                } else {
                    None
                }
            })
            .collect();

        if new_ids.is_empty() {
            return;
        }

        let doc = awareness.doc();

        for client_id in new_ids {
            Self::register_pud_client_id_on_doc(doc, user_id, client_id);
        }
    }

    /// Register a client_id in the "users" PermanentUserData map on the document.
    /// Takes a Doc reference directly to avoid re-locking awareness.
    fn register_pud_client_id_on_doc(doc: &yrs::Doc, user_id: &str, client_id: ClientID) {
        // get_or_insert_map takes a write txn internally, call before any read txn.
        let users_map = doc.get_or_insert_map("users");

        // Check if already registered.
        {
            let txn = doc.transact();
            if let Some(Out::YMap(user_map)) = users_map.get(&txn, user_id) {
                if let Some(Out::YArray(ids_arr)) = user_map.get(&txn, "ids") {
                    for item in ids_arr.iter(&txn) {
                        let existing_id = match &item {
                            Out::Any(yrs::Any::Number(n)) => Some(*n as u64),
                            Out::Any(yrs::Any::BigInt(n)) => Some(*n as u64),
                            _ => None,
                        };
                        if existing_id == Some(client_id.get()) {
                            return;
                        }
                    }
                }
            }
        }

        let mut txn = doc.transact_mut();

        let user_map = match users_map.get(&txn, user_id) {
            Some(Out::YMap(m)) => m,
            _ => users_map.insert(&mut txn, user_id, yrs::MapPrelim::default()),
        };

        let ids_arr = match user_map.get(&txn, "ids") {
            Some(Out::YArray(a)) => a,
            _ => user_map.insert(&mut txn, "ids", yrs::ArrayPrelim::default()),
        };

        // Ensure `ds` exists as an empty YArray so canonical Yjs PUD readers don't crash.
        if !matches!(user_map.get(&txn, "ds"), Some(Out::YArray(_))) {
            user_map.insert(&mut txn, "ds", yrs::ArrayPrelim::default());
        }

        ids_arr.push_back(&mut txn, yrs::Any::Number(client_id.get() as f64));
        tracing::info!(
            user_id,
            client_id = client_id.get(),
            "Registered client_id for user via server-driven PUD"
        );
    }

    /// Check if the token associated with this connection has expired
    fn is_expired(&self) -> bool {
        expired_at(self.expiration_time)
    }

    /// Expiry (epoch millis) of the token this connection was opened with;
    /// `None` if it never expires. The socket owner closes the connection at
    /// this deadline.
    pub fn expiration_time(&self) -> Option<u64> {
        self.expiration_time
    }

    pub async fn send(&self, update: &[u8]) -> Result<(), anyhow::Error> {
        // Check expiration before processing
        if self.is_expired() {
            return Err(anyhow::Error::msg("Token expired"));
        }

        let msg = Message::decode_v1(update)?;
        let result = self.handle_msg(&DefaultProtocol, msg)?;

        if let Some(result) = result {
            let msg = result.encode_v1();
            (self.callback)(&msg);
        }

        Ok(())
    }

    // Adapted from:
    // https://github.com/y-crdt/y-sync/blob/56958e83acfd1f3c09f5dd67cf23c9c72f000707/src/net/conn.rs#L184C1-L222C1
    pub fn handle_msg<P: Protocol>(
        &self,
        protocol: &P,
        msg: Message,
    ) -> Result<Option<Message>, sync::Error> {
        // Check expiration before processing
        if self.is_expired() {
            return Err(sync::Error::PermissionDenied {
                reason: "Token expired".to_string(),
            });
        }

        let can_write = matches!(self.authorization, Authorization::Full);
        let a = &self.awareness;
        match msg {
            Message::Sync(msg) => match msg {
                SyncMessage::SyncStep1(sv) => {
                    let awareness = a.read().unwrap();
                    protocol.handle_sync_step1(&awareness, sv)
                }
                SyncMessage::SyncStep2(update) => {
                    if update.is_empty() {
                        return Ok(None);
                    }

                    if can_write {
                        let mut awareness = a.write().unwrap();
                        let sv_before = self.snapshot_sv(&awareness);
                        let result =
                            protocol.handle_sync_step2(&mut awareness, Update::decode_v1(&update)?);
                        if result.is_ok() {
                            self.register_new_client_ids(&awareness, &sv_before);
                        }
                        result
                    } else {
                        Err(sync::Error::PermissionDenied {
                            reason: "Token does not have write access".to_string(),
                        })
                    }
                }
                SyncMessage::Update(update) => {
                    if update.is_empty() {
                        return Ok(None);
                    }

                    if can_write {
                        let mut awareness = a.write().unwrap();
                        let sv_before = self.snapshot_sv(&awareness);
                        let result =
                            protocol.handle_update(&mut awareness, Update::decode_v1(&update)?);
                        if result.is_ok() {
                            self.register_new_client_ids(&awareness, &sv_before);
                        }
                        result
                    } else {
                        Err(sync::Error::PermissionDenied {
                            reason: "Token does not have write access".to_string(),
                        })
                    }
                }
            },
            Message::Auth(reason) => {
                let awareness = a.read().unwrap();
                protocol.handle_auth(&awareness, reason)
            }
            Message::AwarenessQuery => {
                let awareness = a.read().unwrap();
                protocol.handle_awareness_query(&awareness)
            }
            Message::Awareness(update) => {
                if update.clients.len() == 1 {
                    let client_id = update.clients.keys().next().unwrap();
                    self.client_id.get_or_init(|| *client_id);
                } else {
                    tracing::warn!("Received awareness update with more than one client");
                }
                let mut awareness = a.write().unwrap();
                protocol.handle_awareness_update(&mut awareness, update)
            }
            Message::Custom(SYNC_STATUS_MESSAGE, data) => {
                // Respond to the client with the same payload it sent.
                Ok(Some(Message::Custom(SYNC_STATUS_MESSAGE, data)))
            }
            Message::EventSubscribe(event_types) => {
                if let Ok(mut subscriptions) = self.event_subscriptions.write() {
                    for event_type in &event_types {
                        subscriptions.insert(event_type.clone());
                    }
                    tracing::debug!(
                        "Client subscribed to event types: {:?}. Total subscriptions: {}",
                        event_types,
                        subscriptions.len()
                    );
                } else {
                    tracing::warn!("Failed to acquire event subscriptions lock for subscribe");
                }
                Ok(None)
            }
            Message::EventUnsubscribe(event_types) => {
                if let Ok(mut subscriptions) = self.event_subscriptions.write() {
                    for event_type in &event_types {
                        subscriptions.remove(event_type);
                    }
                    tracing::debug!(
                        "Client unsubscribed from event types: {:?}. Total subscriptions: {}",
                        event_types,
                        subscriptions.len()
                    );
                } else {
                    tracing::warn!("Failed to acquire event subscriptions lock for unsubscribe");
                }
                Ok(None)
            }
            Message::QuerySubdocs(guids) => {
                if guids.is_empty() {
                    return Err(sync::Error::InvalidMessage {
                        reason: "MSG_QUERY_SUBDOCS requires at least one GUID".to_string(),
                    });
                }

                if guids.len() > MAX_QUERY_SUBDOCS_PER_REQUEST {
                    return Err(sync::Error::InvalidMessage {
                        reason: format!(
                            "MSG_QUERY_SUBDOCS accepts at most {} GUIDs per request",
                            MAX_QUERY_SUBDOCS_PER_REQUEST
                        ),
                    });
                }

                // last_query feeds deletion GC, which distinguishes dormant
                // docs (still queried) from deleted ones on a timescale of
                // weeks; a day-granular stamp carries the same signal while
                // making repeat queries no-ops.
                let now = current_time_epoch_millis();
                let day_ms = now - now % MS_PER_DAY;
                let day_val = ciborium::value::Value::Integer(day_ms.into());
                let last_seen_key = ciborium::value::Value::Text("last_seen".to_string());
                let last_query_key = ciborium::value::Value::Text("last_query".to_string());

                // Read under the shared lock: clone only the requested
                // entries (not the whole index), and note which of them
                // still need a stamp so the common repeat-query case never
                // takes the write lock at all.
                let guids_set: HashSet<&str> = guids.iter().map(|s| s.as_str()).collect();
                let (entries, needs_stamp): (
                    Vec<(ciborium::value::Value, ciborium::value::Value)>,
                    HashSet<String>,
                ) = self
                    .sync_kv
                    .as_ref()
                    .map(|kv| {
                        kv.read_metadata(|metadata| {
                            let mut matched = Vec::new();
                            let mut needs_stamp = HashSet::new();
                            let Some(ciborium::value::Value::Map(all_entries)) =
                                metadata.and_then(|m| m.get("subdocs"))
                            else {
                                return (matched, needs_stamp);
                            };
                            for (k, v) in all_entries {
                                let ciborium::value::Value::Text(guid) = k else {
                                    continue;
                                };
                                if !guids_set.contains(guid.as_str()) {
                                    continue;
                                }
                                if let ciborium::value::Value::Map(fields) = v {
                                    let has_legacy_last_seen =
                                        fields.iter().any(|(fk, _)| *fk == last_seen_key);
                                    let stamp_current = fields
                                        .iter()
                                        .any(|(fk, fv)| *fk == last_query_key && *fv == day_val);
                                    if has_legacy_last_seen || !stamp_current {
                                        needs_stamp.insert(guid.clone());
                                    }
                                }
                                matched.push((k.clone(), v.clone()));
                            }
                            (matched, needs_stamp)
                        })
                    })
                    .unwrap_or_default();

                // Build response: {data: {guid: {snapshot, last_seen}, ...}}
                let mut response_entries = Vec::new();
                for (k, v) in &entries {
                    if let ciborium::value::Value::Map(fields) = v {
                        let mut snapshot = None;
                        let mut last_edit = None;
                        let mut legacy_last_seen = None;

                        for (fk, fv) in fields {
                            if let ciborium::value::Value::Text(fname) = fk {
                                match fname.as_str() {
                                    "snapshot" => {
                                        if let ciborium::value::Value::Bytes(_) = fv {
                                            snapshot = Some(fv.clone());
                                        }
                                    }
                                    "last_edit" => {
                                        if let ciborium::value::Value::Integer(_) = fv {
                                            last_edit = Some(fv.clone());
                                        }
                                    }
                                    "last_seen" => {
                                        if let ciborium::value::Value::Integer(_) = fv {
                                            legacy_last_seen = Some(fv.clone());
                                        }
                                    }
                                    _ => {}
                                }
                            }
                        }

                        if let (Some(snapshot), Some(last_seen)) =
                            (snapshot, last_edit.or(legacy_last_seen))
                        {
                            response_entries.push((
                                k.clone(),
                                ciborium::value::Value::Map(vec![
                                    (
                                        ciborium::value::Value::Text("snapshot".to_string()),
                                        snapshot,
                                    ),
                                    (
                                        ciborium::value::Value::Text("last_seen".to_string()),
                                        last_seen,
                                    ),
                                ]),
                            ));
                        }
                    }
                }

                // Stamp last_query for entries returned by this query, but
                // only take the write lock when the read pass saw a stamp
                // that actually needs to advance (or a legacy last_seen to
                // clear). Repeat queries within the same day stay read-only.
                let stamp_guids: std::collections::HashSet<String> = response_entries
                    .iter()
                    .filter_map(|(k, _)| {
                        if let ciborium::value::Value::Text(guid) = k {
                            needs_stamp.contains(guid).then(|| guid.clone())
                        } else {
                            None
                        }
                    })
                    .collect();

                if !stamp_guids.is_empty() {
                    if let Some(kv) = self.sync_kv.as_ref() {
                        // Mutate under the metadata write lock: this runs
                        // concurrently with subdoc snapshot updates on the
                        // same parent, and an unlocked read-modify-write
                        // here erases snapshots written between the read
                        // and the write. The closure re-checks each field
                        // because state may have moved between the read
                        // pass above and this write.
                        kv.with_metadata_if_changed(|metadata| {
                            let mut changed = false;
                            if let Some(ciborium::value::Value::Map(ref mut all_entries)) =
                                metadata.get_mut("subdocs")
                            {
                                for (entry_key, entry_val) in all_entries {
                                    let guid = if let ciborium::value::Value::Text(guid) = entry_key
                                    {
                                        guid
                                    } else {
                                        continue;
                                    };

                                    if stamp_guids.contains(guid) {
                                        if let ciborium::value::Value::Map(ref mut fields) =
                                            entry_val
                                        {
                                            let fields_before = fields.len();
                                            fields.retain(|(k, _)| *k != last_seen_key);
                                            if fields.len() != fields_before {
                                                changed = true;
                                            }
                                            if let Some(field) = fields
                                                .iter_mut()
                                                .find(|(k, _)| *k == last_query_key)
                                            {
                                                if field.1 != day_val {
                                                    field.1 = day_val.clone();
                                                    changed = true;
                                                }
                                            } else {
                                                fields.push((
                                                    last_query_key.clone(),
                                                    day_val.clone(),
                                                ));
                                                changed = true;
                                            }
                                        }
                                    }
                                }
                            }
                            changed
                        });
                    }
                }

                let empty_response = ciborium::value::Value::Map(vec![(
                    ciborium::value::Value::Text("data".to_string()),
                    ciborium::value::Value::Map(Vec::new()),
                )]);
                let response = ciborium::value::Value::Map(vec![(
                    ciborium::value::Value::Text("data".to_string()),
                    ciborium::value::Value::Map(response_entries),
                )]);
                let mut cbor_bytes = Vec::new();
                ciborium::ser::into_writer(&response, &mut cbor_bytes).unwrap_or_else(|_| {
                    cbor_bytes.clear();
                    ciborium::ser::into_writer(&empty_response, &mut cbor_bytes).unwrap();
                });
                Ok(Some(Message::Subdocs(cbor_bytes)))
            }
            Message::Subdocs(_) => {
                // Server shouldn't receive Subdocs from clients
                tracing::warn!("Client sent Subdocs message to server, ignoring");
                Ok(None)
            }
            Message::Event(_event_data) => {
                // Clients shouldn't send events to the server, but we'll just log and ignore
                tracing::warn!("Client sent event message to server, ignoring");
                Ok(None)
            }
            Message::Custom(tag, data) => {
                let mut awareness = a.write().unwrap();
                protocol.missing_handle(&mut awareness, tag, data)
            }
        }
    }

    /// Send an event to this connection if it's subscribed to the event type
    pub fn send_event(&self, event: &EventMessage) -> Result<(), anyhow::Error> {
        // An expired connection gets no more server-pushed data.
        if self.is_expired() {
            return Ok(());
        }

        // Check if connection is subscribed to this event type
        let is_subscribed = if let Ok(subscriptions) = self.event_subscriptions.read() {
            subscriptions.contains(&event.event_type)
        } else {
            tracing::warn!("Failed to acquire event subscriptions lock for send_event");
            return Ok(()); // Fail silently
        };

        if !is_subscribed {
            return Ok(()); // Not subscribed, don't send
        }

        // Serialize event to CBOR
        let cbor_data = event
            .to_cbor()
            .map_err(|e| anyhow::anyhow!("Failed to serialize event to CBOR: {:?}", e))?;

        // Send as Event message
        let msg = Message::Event(cbor_data).encode_v1();
        (self.callback)(&msg);

        tracing::debug!(
            "Sent event {} (type: {}) to client",
            event.event_id,
            event.event_type
        );

        Ok(())
    }

    /// Get the event types this connection is subscribed to
    pub fn get_event_subscriptions(&self) -> HashSet<String> {
        self.event_subscriptions
            .read()
            .map(|subscriptions| subscriptions.clone())
            .unwrap_or_default()
    }
}

/// `true` once a token with this expiry (epoch millis) has expired; never for
/// `None`. Shared by the request path and the server-push callbacks so an
/// expired connection neither writes nor receives.
fn expired_at(expiration_time: Option<u64>) -> bool {
    expiration_time.is_some_and(|exp| current_time_epoch_millis() > exp)
}

impl Drop for DocConnection {
    fn drop(&mut self) {
        self.closed.set(()).unwrap();

        // If this client had an awareness state, remove it.
        if let Some(client_id) = self.client_id.get() {
            let mut awareness = self.awareness.write().unwrap();
            awareness.remove_state(*client_id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sync::{DefaultProtocol, EventMessage, Message, SyncMessage};

    #[tokio::test]
    async fn test_query_subdocs_returns_snapshot_envelope() {
        let sync_kv = Arc::new(SyncKv::new(None, "parent_doc", || ()).await.unwrap());
        let snapshot_bytes = vec![10, 20, 30];
        let legacy_state_vector_bytes = vec![1, 2, 3];

        let mut metadata = std::collections::BTreeMap::new();
        metadata.insert(
            "subdocs".to_string(),
            ciborium::value::Value::Map(vec![(
                ciborium::value::Value::Text("subdoc-abc".to_string()),
                ciborium::value::Value::Map(vec![
                    (
                        ciborium::value::Value::Text("state_vector".to_string()),
                        ciborium::value::Value::Bytes(legacy_state_vector_bytes),
                    ),
                    (
                        ciborium::value::Value::Text("snapshot".to_string()),
                        ciborium::value::Value::Bytes(snapshot_bytes.clone()),
                    ),
                    (
                        ciborium::value::Value::Text("last_edit".to_string()),
                        ciborium::value::Value::Integer(123.into()),
                    ),
                    (
                        ciborium::value::Value::Text("last_seen".to_string()),
                        ciborium::value::Value::Integer(99.into()),
                    ),
                ]),
            )]),
        );
        sync_kv.set_metadata(metadata);

        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));
        let mut connection = DocConnection::new(awareness, Authorization::Full, |_| {});
        connection.set_sync_kv(sync_kv.clone());
        let before_query = current_time_epoch_millis();

        let result = connection
            .handle_msg(
                &DefaultProtocol,
                Message::QuerySubdocs(vec!["subdoc-abc".to_string()]),
            )
            .unwrap();

        if let Some(Message::Subdocs(cbor_bytes)) = result {
            let decoded: ciborium::value::Value =
                ciborium::de::from_reader(&cbor_bytes[..]).unwrap();
            assert_eq!(
                decoded,
                ciborium::value::Value::Map(vec![(
                    ciborium::value::Value::Text("data".to_string()),
                    ciborium::value::Value::Map(vec![(
                        ciborium::value::Value::Text("subdoc-abc".to_string()),
                        ciborium::value::Value::Map(vec![
                            (
                                ciborium::value::Value::Text("snapshot".to_string()),
                                ciborium::value::Value::Bytes(snapshot_bytes),
                            ),
                            (
                                ciborium::value::Value::Text("last_seen".to_string()),
                                ciborium::value::Value::Integer(123.into()),
                            ),
                        ]),
                    )]),
                )])
            );
        } else {
            panic!("Expected Subdocs response");
        }

        let metadata = sync_kv.get_metadata().unwrap();
        let subdocs = metadata.get("subdocs").unwrap();
        if let ciborium::value::Value::Map(entries) = subdocs {
            if let ciborium::value::Value::Map(fields) = &entries[0].1 {
                assert_eq!(
                    fields
                        .iter()
                        .find(|(k, _)| {
                            *k == ciborium::value::Value::Text("last_edit".to_string())
                        })
                        .unwrap()
                        .1,
                    ciborium::value::Value::Integer(123.into())
                );
                let last_query = fields
                    .iter()
                    .find(|(k, _)| *k == ciborium::value::Value::Text("last_query".to_string()))
                    .unwrap();
                if let ciborium::value::Value::Integer(ts) = &last_query.1 {
                    let ts: u64 = (*ts).try_into().unwrap();
                    let day = |t: u64| t - t % MS_PER_DAY;
                    // The stamp is day-granular; allow for a UTC midnight
                    // crossing between capturing before_query and the query.
                    assert!(ts == day(before_query) || ts == day(current_time_epoch_millis()));
                } else {
                    panic!("Expected Integer timestamp");
                }
                assert!(fields
                    .iter()
                    .all(|(k, _)| *k != ciborium::value::Value::Text("last_seen".to_string())));
            } else {
                panic!("Expected subdoc metadata map");
            }
        } else {
            panic!("Expected subdocs map");
        }
    }

    #[tokio::test]
    async fn test_query_subdocs_repeat_query_does_not_redirty() {
        let dirty_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let dirty_count_for_callback = dirty_count.clone();
        let sync_kv = Arc::new(
            SyncKv::new(None, "parent_doc", move || {
                dirty_count_for_callback.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            })
            .await
            .unwrap(),
        );

        let mut metadata = std::collections::BTreeMap::new();
        metadata.insert(
            "subdocs".to_string(),
            ciborium::value::Value::Map(vec![(
                ciborium::value::Value::Text("subdoc-abc".to_string()),
                ciborium::value::Value::Map(vec![
                    (
                        ciborium::value::Value::Text("snapshot".to_string()),
                        ciborium::value::Value::Bytes(vec![10, 20, 30]),
                    ),
                    (
                        ciborium::value::Value::Text("last_edit".to_string()),
                        ciborium::value::Value::Integer(123.into()),
                    ),
                ]),
            )]),
        );
        sync_kv.set_metadata(metadata);
        sync_kv.persist().await.unwrap(); // clear dirty from seeding

        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));
        let mut connection = DocConnection::new(awareness, Authorization::Full, |_| {});
        connection.set_sync_kv(sync_kv.clone());

        // First query stamps last_query for the day and dirties the doc.
        connection
            .handle_msg(
                &DefaultProtocol,
                Message::QuerySubdocs(vec!["subdoc-abc".to_string()]),
            )
            .unwrap();
        let after_first = dirty_count.load(std::sync::atomic::Ordering::SeqCst);
        sync_kv.persist().await.unwrap(); // clear dirty again

        // A repeat query on the same day changes nothing and must not
        // re-dirty the document.
        connection
            .handle_msg(
                &DefaultProtocol,
                Message::QuerySubdocs(vec!["subdoc-abc".to_string()]),
            )
            .unwrap();
        assert_eq!(
            dirty_count.load(std::sync::atomic::Ordering::SeqCst),
            after_first
        );
    }

    #[test]
    fn test_query_subdocs_rejects_empty_request() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));
        let connection = DocConnection::new(awareness, Authorization::Full, |_| {});

        let result = connection.handle_msg(&DefaultProtocol, Message::QuerySubdocs(vec![]));

        if let Err(sync::Error::InvalidMessage { reason }) = result {
            assert!(reason.contains("requires at least one GUID"));
        } else {
            panic!("Expected InvalidMessage error for empty subdoc query");
        }
    }

    #[test]
    fn test_query_subdocs_rejects_oversized_request() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));
        let connection = DocConnection::new(awareness, Authorization::Full, |_| {});
        let guids = (0..=MAX_QUERY_SUBDOCS_PER_REQUEST)
            .map(|idx| format!("subdoc-{}", idx))
            .collect();

        let result = connection.handle_msg(&DefaultProtocol, Message::QuerySubdocs(guids));

        if let Err(sync::Error::InvalidMessage { reason }) = result {
            assert!(reason.contains("at most"));
        } else {
            panic!("Expected InvalidMessage error for oversized subdoc query");
        }
    }

    #[test]
    fn test_doc_connection_event_subscriptions() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));
        let (tx, _rx) = std::sync::mpsc::channel();

        let connection = DocConnection::new(awareness, Authorization::Full, move |_| {
            // Mock callback
            tx.send(()).unwrap();
        });

        // Initially no subscriptions
        assert!(connection.get_event_subscriptions().is_empty());

        // Subscribe to some event types
        let subscribe_msg = Message::EventSubscribe(vec![
            "document.updated".to_string(),
            "user.joined".to_string(),
        ]);

        let result = connection.handle_msg(&DefaultProtocol, subscribe_msg);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none());

        // Check subscriptions
        let subscriptions = connection.get_event_subscriptions();
        assert_eq!(subscriptions.len(), 2);
        assert!(subscriptions.contains("document.updated"));
        assert!(subscriptions.contains("user.joined"));

        // Unsubscribe from one event type
        let unsubscribe_msg = Message::EventUnsubscribe(vec!["user.joined".to_string()]);

        let result = connection.handle_msg(&DefaultProtocol, unsubscribe_msg);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none());

        // Check subscriptions after unsubscribe
        let subscriptions = connection.get_event_subscriptions();
        assert_eq!(subscriptions.len(), 1);
        assert!(subscriptions.contains("document.updated"));
        assert!(!subscriptions.contains("user.joined"));
    }

    #[test]
    fn test_doc_connection_send_event() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));
        let (tx, rx) = std::sync::mpsc::channel();

        let connection = Arc::new(DocConnection::new(
            awareness,
            Authorization::Full,
            move |bytes| {
                tx.send(bytes.to_vec()).unwrap();
            },
        ));

        // Subscribe to document.updated events
        let subscribe_msg = Message::EventSubscribe(vec!["document.updated".to_string()]);
        let result = connection.handle_msg(&DefaultProtocol, subscribe_msg);
        assert!(result.is_ok());

        // Create an event
        let event = EventMessage {
            event_id: "evt_test123".to_string(),
            event_type: "document.updated".to_string(),
            doc_id: "test_doc".to_string(),
            timestamp: 1640995200000,
            user: Some("test@example.com".to_string()),
            metadata: Some(serde_json::json!({"version": 2})),
            update: None,
        };

        // Send the event
        let result = connection.send_event(&event);
        assert!(result.is_ok());

        // Check that messages were sent in the correct order
        let _sync_step1 = rx.recv().unwrap(); // Initial SyncStep1
        let _awareness = rx.recv().unwrap(); // Initial Awareness
        let event_bytes = rx.recv().unwrap(); // From send_event

        // Decode the sent message
        let decoded_msg = Message::decode_v1(&event_bytes).unwrap();
        if let Message::Event(cbor_data) = decoded_msg {
            let decoded_event = EventMessage::from_cbor(&cbor_data).unwrap();
            assert_eq!(decoded_event, event);
        } else {
            panic!("Expected Event message");
        }
    }

    #[test]
    fn test_doc_connection_send_event_not_subscribed() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));
        let (tx, rx) = std::sync::mpsc::channel();

        let connection = Arc::new(DocConnection::new(
            awareness,
            Authorization::Full,
            move |bytes| {
                tx.send(bytes.to_vec()).unwrap();
            },
        ));

        // Don't subscribe to any events

        // Create an event
        let event = EventMessage {
            event_id: "evt_test123".to_string(),
            event_type: "document.updated".to_string(),
            doc_id: "test_doc".to_string(),
            timestamp: 1640995200000,
            user: None,
            metadata: None,
            update: None,
        };

        // Send the event - should succeed but not send anything
        let result = connection.send_event(&event);
        assert!(result.is_ok());

        // Check that no message was sent (only the initial handshake messages)
        let _sent_bytes = rx.recv().unwrap(); // Initial SyncStep1
        let _sent_bytes2 = rx.recv().unwrap(); // Initial Awareness

        // No more messages should be available immediately
        assert!(rx.try_recv().is_err());
    }

    #[test]
    fn test_doc_connection_handles_client_events() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        let connection = DocConnection::new(awareness, Authorization::Full, |_| {
            // Mock callback
        });

        // Client shouldn't send events to server, but we handle it gracefully
        let event = EventMessage {
            event_id: "evt_from_client".to_string(),
            event_type: "document.updated".to_string(),
            doc_id: "test_doc".to_string(),
            timestamp: 1640995200000,
            user: None,
            metadata: None,
            update: None,
        };

        let cbor_data = event.to_cbor().unwrap();
        let event_msg = Message::Event(cbor_data);

        let result = connection.handle_msg(&DefaultProtocol, event_msg);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none());
    }

    #[test]
    fn test_doc_connection_expiration_not_expired() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        // Set expiration to far future (1 hour from now)
        let future_time = current_time_epoch_millis() + 3_600_000;

        let connection = DocConnection::new_with_expiration(
            awareness,
            Authorization::Full,
            Some(future_time),
            |_| {
                // Mock callback
            },
        );

        // Token should not be expired
        assert!(!connection.is_expired());

        // Should be able to handle messages
        let subscribe_msg = Message::EventSubscribe(vec!["test".to_string()]);
        let result = connection.handle_msg(&DefaultProtocol, subscribe_msg);
        assert!(result.is_ok());
    }

    #[test]
    fn test_doc_connection_expiration_expired() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        // Set expiration to past time
        let past_time = current_time_epoch_millis() - 1000;

        let connection = DocConnection::new_with_expiration(
            awareness,
            Authorization::Full,
            Some(past_time),
            |_| {
                // Mock callback
            },
        );

        // Token should be expired
        assert!(connection.is_expired());

        // Should fail to handle messages
        let subscribe_msg = Message::EventSubscribe(vec!["test".to_string()]);
        let result = connection.handle_msg(&DefaultProtocol, subscribe_msg);
        assert!(result.is_err());

        if let Err(sync::Error::PermissionDenied { reason }) = result {
            assert_eq!(reason, "Token expired");
        } else {
            panic!("Expected PermissionDenied error with 'Token expired' reason");
        }
    }

    #[test]
    fn test_doc_connection_no_expiration() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        let connection = DocConnection::new_with_expiration(
            awareness,
            Authorization::Full,
            None, // No expiration
            |_| {
                // Mock callback
            },
        );

        // Token should never be expired
        assert!(!connection.is_expired());

        // Should be able to handle messages
        let subscribe_msg = Message::EventSubscribe(vec!["test".to_string()]);
        let result = connection.handle_msg(&DefaultProtocol, subscribe_msg);
        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_doc_connection_send_expired() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        // Set expiration to past time
        let past_time = current_time_epoch_millis() - 1000;

        let connection = DocConnection::new_with_expiration(
            awareness,
            Authorization::Full,
            Some(past_time),
            |_| {
                // Mock callback
            },
        );

        // Should fail to send messages when expired
        let dummy_update = vec![1, 2, 3, 4]; // Dummy binary data
        let result = connection.send(&dummy_update).await;
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("Token expired"));
    }

    #[tokio::test]
    async fn test_doc_connection_send_not_expired() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        // Set expiration to far future
        let future_time = current_time_epoch_millis() + 3_600_000;

        let connection = DocConnection::new_with_expiration(
            awareness,
            Authorization::Full,
            Some(future_time),
            |_| {
                // Mock callback
            },
        );

        // Create a valid sync message (SyncStep1 with empty state vector)
        let sv = yrs::StateVector::default();
        let msg = Message::Sync(crate::sync::SyncMessage::SyncStep1(sv));
        let encoded = msg.encode_v1();

        // Should succeed when not expired
        let result = connection.send(&encoded).await;
        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_doc_connection_send_empty_sync_step2_is_noop() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        let connection = DocConnection::new(awareness, Authorization::Full, |_| {});
        let msg = Message::Sync(SyncMessage::SyncStep2(Vec::new()));
        let encoded = msg.encode_v1();

        let result = connection.send(&encoded).await;
        assert!(result.is_ok());
    }

    #[test]
    fn test_doc_connection_allows_empty_sync_update_from_read_only() {
        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc)));

        let connection = DocConnection::new(awareness, Authorization::ReadOnly, |_| {});

        let sync_step2 = Message::Sync(SyncMessage::SyncStep2(Vec::new()));
        let result = connection.handle_msg(&DefaultProtocol, sync_step2);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none());

        let update = Message::Sync(SyncMessage::Update(Vec::new()));
        let result = connection.handle_msg(&DefaultProtocol, update);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none());
    }

    /// Server-push after token expiry: an owner edit must not reach a
    /// connection whose token has expired (positive control: a live one does).
    #[test]
    fn test_expired_connection_receives_no_doc_broadcast() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        use yrs::{Map, Transact};

        let doc = yrs::Doc::new();
        let awareness = Arc::new(RwLock::new(Awareness::new(doc.clone())));
        let now = current_time_epoch_millis();
        let live_count = Arc::new(AtomicUsize::new(0));
        let expired_count = Arc::new(AtomicUsize::new(0));
        let live = {
            let c = live_count.clone();
            DocConnection::new_with_expiration(
                awareness.clone(),
                Authorization::Full,
                Some(now + 60_000),
                move |_| {
                    c.fetch_add(1, Ordering::SeqCst);
                },
            )
        };
        let expired = {
            let c = expired_count.clone();
            DocConnection::new_with_expiration(
                awareness.clone(),
                Authorization::Full,
                Some(now - 1),
                move |_| {
                    c.fetch_add(1, Ordering::SeqCst);
                },
            )
        };
        let live_before = live_count.load(Ordering::SeqCst);
        let expired_before = expired_count.load(Ordering::SeqCst);

        let map = doc.get_or_insert_map("probe");
        map.insert(&mut doc.transact_mut(), "owner", "edit");

        assert!(live_count.load(Ordering::SeqCst) > live_before);
        assert_eq!(expired_count.load(Ordering::SeqCst), expired_before);
        assert_eq!(expired.expiration_time(), Some(now - 1));
        drop(live);
    }
}
