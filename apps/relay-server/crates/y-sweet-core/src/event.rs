use crate::api_types::NANOID_ALPHABET;
use crate::metrics::RelayMetrics;
use crate::sync::EventMessage;
use crate::sync_kv::SyncKv;
use crate::webhook::WebhookConfig;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};
use tokio::sync::mpsc;
use tokio::time::timeout;
use tracing::{debug, error, info};

/// Convert CBOR metadata values to JSON values for event serialization
fn cbor_metadata_to_json(
    cbor_metadata: &BTreeMap<String, ciborium::value::Value>,
) -> Result<BTreeMap<String, serde_json::Value>, Box<dyn std::error::Error>> {
    let mut json_metadata = BTreeMap::new();

    for (key, cbor_value) in cbor_metadata {
        let json_value = cbor_value_to_json_value(cbor_value)?;
        json_metadata.insert(key.clone(), json_value);
    }

    Ok(json_metadata)
}

/// Convert a single CBOR value to a JSON value
fn cbor_value_to_json_value(
    cbor_value: &ciborium::value::Value,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    match cbor_value {
        ciborium::value::Value::Integer(i) => {
            // CBOR integers can be larger than i64, so we need to handle the conversion carefully
            if let Ok(i64_val) = TryInto::<i64>::try_into(*i) {
                Ok(serde_json::Value::Number(serde_json::Number::from(i64_val)))
            } else if let Ok(u64_val) = TryInto::<u64>::try_into(*i) {
                // Try to convert to u64 if i64 fails
                if let Some(num) = serde_json::Number::from_f64(u64_val as f64) {
                    Ok(serde_json::Value::Number(num))
                } else {
                    Ok(serde_json::Value::Null)
                }
            } else {
                // If conversion fails, convert to null
                Ok(serde_json::Value::Null)
            }
        }
        ciborium::value::Value::Text(s) => Ok(serde_json::Value::String(s.clone())),
        ciborium::value::Value::Bool(b) => Ok(serde_json::Value::Bool(*b)),
        ciborium::value::Value::Null => Ok(serde_json::Value::Null),
        ciborium::value::Value::Array(arr) => {
            let json_array: Result<Vec<_>, _> = arr.iter().map(cbor_value_to_json_value).collect();
            Ok(serde_json::Value::Array(json_array?))
        }
        ciborium::value::Value::Map(map) => {
            let mut json_obj = serde_json::Map::new();
            for (k, v) in map {
                if let ciborium::value::Value::Text(key_str) = k {
                    let json_val = cbor_value_to_json_value(v)?;
                    json_obj.insert(key_str.clone(), json_val);
                } else {
                    return Err("Map keys must be strings for JSON conversion".into());
                }
            }
            Ok(serde_json::Value::Object(json_obj))
        }
        ciborium::value::Value::Float(f) => {
            if let Some(num) = serde_json::Number::from_f64(*f) {
                Ok(serde_json::Value::Number(num))
            } else {
                Err("Invalid floating point number for JSON".into())
            }
        }
        _ => Ok(serde_json::Value::Null),
    }
}

/// Event payloads contain only business data
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DocumentUpdatedEvent {
    pub doc_id: String,
    pub user: Option<String>,
    pub metadata: BTreeMap<String, serde_json::Value>,
    #[serde(skip)] // Don't serialize the raw update data to JSON
    pub update: Option<Vec<u8>>,
    #[serde(skip)] // Internal use only: encoded snapshot after this update
    pub snapshot: Option<Vec<u8>>,
}

impl DocumentUpdatedEvent {
    /// Create a new document updated event payload
    pub fn new(doc_id: String) -> Self {
        Self {
            doc_id,
            user: None,
            metadata: BTreeMap::new(),
            update: None,
            snapshot: None,
        }
    }

    /// Builder method to add user
    pub fn with_user(mut self, user: String) -> Self {
        self.user = Some(user);
        self
    }

    /// Builder method to add Yjs update data
    pub fn with_update(mut self, update: Vec<u8>) -> Self {
        self.update = Some(update);
        self
    }

    /// Builder method to add encoded Yjs snapshot
    pub fn with_snapshot(mut self, snapshot: Vec<u8>) -> Self {
        self.snapshot = Some(snapshot);
        self
    }

    /// Builder method to add metadata from SyncKv.
    ///
    /// Runs under the shared metadata lock without cloning the map, and
    /// excludes the `subdocs` index: it can hold thousands of entries whose
    /// snapshot bytes have no JSON representation, and clients read subdoc
    /// heads through MSG_QUERY_SUBDOCS rather than event payloads.
    pub fn with_metadata(mut self, sync_kv: &SyncKv) -> Self {
        const EXCLUDED_KEYS: &[&str] = &["subdocs"];
        self.metadata = sync_kv.read_metadata(|metadata| {
            let mut json_metadata = BTreeMap::new();
            if let Some(map) = metadata {
                for (key, cbor_value) in map {
                    if EXCLUDED_KEYS.contains(&key.as_str()) {
                        continue;
                    }
                    match cbor_value_to_json_value(cbor_value) {
                        Ok(json_value) => {
                            json_metadata.insert(key.clone(), json_value);
                        }
                        Err(e) => {
                            error!("Failed to convert metadata field {} to JSON: {}", key, e);
                        }
                    }
                }
            }
            json_metadata
        });
        self
    }

    /// Get the event type identifier
    pub fn event_type() -> &'static str {
        "document.updated"
    }
}

/// The envelope contains only routing and transport metadata
#[derive(Clone, Debug)]
pub struct EventEnvelope {
    pub event_id: String,
    pub event_type: String,
    pub channel: String, // Routing channel
    pub timestamp: chrono::DateTime<chrono::Utc>,
    pub event: DocumentUpdatedEvent, // Raw event data - serialize at dispatch time
}

impl EventEnvelope {
    /// Create an envelope for a document updated event
    /// Channel and event are provided separately  
    pub fn new(channel: String, event: DocumentUpdatedEvent) -> Self {
        Self {
            event_id: format!("evt_{}", nanoid::nanoid!(21, NANOID_ALPHABET)),
            event_type: DocumentUpdatedEvent::event_type().to_string(),
            channel,
            timestamp: chrono::Utc::now(),
            event,
        }
    }

    /// Create an envelope with explicit timestamp (for testing)
    pub fn new_with_timestamp(
        channel: String,
        event: DocumentUpdatedEvent,
        timestamp: chrono::DateTime<chrono::Utc>,
    ) -> Self {
        Self {
            event_id: format!("evt_{}", nanoid::nanoid!(21, NANOID_ALPHABET)),
            event_type: DocumentUpdatedEvent::event_type().to_string(),
            channel,
            timestamp,
            event,
        }
    }
}

/// Trait for dispatching events to registered listeners
pub trait EventDispatcher: Send + Sync {
    /// Send an event envelope to all registered listeners
    fn send_event(&self, envelope: EventEnvelope);

    /// Gracefully shutdown the dispatcher
    fn shutdown(&self);
}

/// Transport-specific event senders
pub trait EventSender: Send + Sync + std::any::Any {
    /// Send an event envelope using this transport
    fn send_event(&self, envelope: EventEnvelope);

    /// Gracefully shutdown this sender
    fn shutdown(&self);
}

/// Messages sent to WebSocket clients
#[derive(Serialize, Debug, Clone)]
#[serde(tag = "type")]
pub enum ServerMessage {
    #[serde(rename = "event")]
    Event {
        #[serde(rename = "eventId")]
        event_id: String,

        #[serde(rename = "eventType")]
        event_type: String,

        channel: String,
        timestamp: String,

        payload: serde_json::Value, // The serialized DocumentUpdatedEvent
    },

    #[serde(rename = "pong")]
    Pong,

    #[serde(rename = "error")]
    Error { message: String },
}

impl From<EventEnvelope> for ServerMessage {
    fn from(envelope: EventEnvelope) -> Self {
        ServerMessage::Event {
            event_id: envelope.event_id,
            event_type: envelope.event_type,
            channel: envelope.channel,
            timestamp: envelope.timestamp.to_rfc3339(),
            payload: serde_json::to_value(envelope.event)
                .expect("DocumentUpdatedEvent should always serialize"),
        }
    }
}

/// Unified event dispatcher that fans out events to all transport-specific senders
pub struct UnifiedEventDispatcher {
    senders: Vec<Arc<dyn EventSender>>,
    metrics: Arc<RelayMetrics>,
}

impl UnifiedEventDispatcher {
    /// Create a new unified dispatcher with the given senders
    pub fn new(senders: Vec<Arc<dyn EventSender>>, metrics: Arc<RelayMetrics>) -> Self {
        debug!(
            "Created UnifiedEventDispatcher with {} senders",
            senders.len()
        );
        Self { senders, metrics }
    }
}

impl EventDispatcher for UnifiedEventDispatcher {
    fn send_event(&self, envelope: EventEnvelope) {
        debug!(
            "Dispatching event {} for channel {} to {} senders",
            envelope.event_id,
            envelope.channel,
            self.senders.len()
        );

        // Record event dispatch metrics
        for sender in &self.senders {
            let sender_type = if sender.type_id() == std::any::TypeId::of::<WebhookSender>() {
                "webhook"
            } else if sender.type_id() == std::any::TypeId::of::<SyncProtocolEventSender>() {
                "sync_protocol"
            } else if sender.type_id() == std::any::TypeId::of::<DebouncedSyncProtocolEventSender>()
            {
                "debounced_sync_protocol"
            } else {
                "unknown"
            };

            self.metrics
                .record_event_dispatched(&envelope.event_type, sender_type);
        }

        // Fanout to all delivery mechanisms
        for sender in &self.senders {
            sender.send_event(envelope.clone());
        }
    }

    fn shutdown(&self) {
        debug!(
            "Shutting down UnifiedEventDispatcher with {} senders",
            self.senders.len()
        );
        for sender in &self.senders {
            sender.shutdown();
        }
    }
}

/// HTTP webhook payload format
#[derive(Serialize, Debug, Clone)]
pub struct WebhookPayload {
    #[serde(rename = "eventType")]
    pub event_type: String,
    #[serde(rename = "eventId")]
    pub event_id: String,
    pub payload: serde_json::Value,
}

impl From<EventEnvelope> for WebhookPayload {
    fn from(envelope: EventEnvelope) -> Self {
        let mut payload = serde_json::to_value(envelope.event)
            .expect("DocumentUpdatedEvent should always serialize");
        payload["timestamp"] = serde_json::Value::String(envelope.timestamp.to_rfc3339());
        WebhookPayload {
            event_type: envelope.event_type,
            event_id: envelope.event_id,
            payload,
        }
    }
}

/// HTTP webhook event sender
pub struct WebhookSender {
    configs: Vec<WebhookConfig>,
    queues: HashMap<String, mpsc::UnboundedSender<EventEnvelope>>,
    shutdown_senders: Vec<mpsc::UnboundedSender<()>>,
    metrics: Arc<RelayMetrics>,
}

impl WebhookSender {
    pub fn new(
        configs: Vec<WebhookConfig>,
        metrics: Arc<RelayMetrics>,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let mut queues = HashMap::new();
        let mut shutdown_senders = Vec::new();

        for config in &configs {
            let (tx, rx) = mpsc::unbounded_channel();
            let (shutdown_tx, shutdown_rx) = mpsc::unbounded_channel();

            queues.insert(config.prefix.clone(), tx);
            shutdown_senders.push(shutdown_tx);

            // Set initial metrics
            metrics.set_active_dispatchers(&config.prefix, 1);
            metrics.set_queue_length(&config.prefix, 0);

            // Spawn worker task for this prefix
            let config_clone = config.clone();
            let metrics_clone = metrics.clone();
            tokio::spawn(async move {
                Self::webhook_worker(config_clone, rx, shutdown_rx, metrics_clone).await;
            });
        }

        Ok(WebhookSender {
            configs,
            queues,
            shutdown_senders,
            metrics,
        })
    }

    /// Get access to the metrics instance
    pub fn metrics(&self) -> &Arc<RelayMetrics> {
        &self.metrics
    }

    fn find_matching_prefixes(&self, channel: &str) -> Vec<String> {
        let mut matches: Vec<String> = self
            .configs
            .iter()
            .filter(|config| channel.starts_with(&config.prefix))
            .map(|config| config.prefix.clone())
            .collect();

        // Sort by prefix length (longest first) for consistent ordering
        matches.sort_by(|a, b| b.len().cmp(&a.len()));
        matches.dedup();
        matches
    }

    async fn webhook_worker(
        config: WebhookConfig,
        mut rx: mpsc::UnboundedReceiver<EventEnvelope>,
        mut shutdown_rx: mpsc::UnboundedReceiver<()>,
        metrics: Arc<RelayMetrics>,
    ) {
        let client = Client::builder()
            .timeout(Duration::from_millis(config.timeout_ms))
            .pool_max_idle_per_host(5)
            .user_agent("y-sweet-webhook/1.0.0")
            .build()
            .unwrap_or_else(|e| {
                error!(
                    "Failed to create HTTP client for prefix '{}': {}",
                    config.prefix, e
                );
                panic!("HTTP client creation failed");
            });

        loop {
            // Check shutdown first
            if shutdown_rx.try_recv().is_ok() {
                info!("Webhook worker shutting down for prefix: {}", config.prefix);
                break;
            }

            // Then check for events with timeout
            match tokio::time::timeout(Duration::from_millis(100), rx.recv()).await {
                Ok(Some(envelope)) => {
                    if let Err(e) =
                        Self::send_single_webhook(&client, &config, &envelope, &metrics).await
                    {
                        error!(
                            "Failed to send webhook for event {} with prefix '{}': {}",
                            envelope.event_id, config.prefix, e
                        );
                    }
                }
                Ok(None) => {
                    break; // Channel closed
                }
                Err(_) => {
                    // Timeout - continue loop to check shutdown again
                    continue;
                }
            }
        }
    }

    async fn send_single_webhook(
        client: &Client,
        config: &WebhookConfig,
        envelope: &EventEnvelope,
        metrics: &RelayMetrics,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let start_time = Instant::now();

        let payload: WebhookPayload = envelope.clone().into();

        debug!(
            "Sending webhook for event {} (channel {}) to prefix '{}'",
            envelope.event_id, envelope.channel, config.prefix
        );

        let mut request = client
            .post(&config.url)
            .header("Content-Type", "application/json");

        if let Some(auth_token) = &config.auth_token {
            request = request.header("Authorization", format!("Bearer {}", auth_token));
        }

        let request = request.json(&payload);

        let result = timeout(Duration::from_millis(config.timeout_ms), request.send())
            .await
            .map_err(|_| format!("Webhook request timed out after {}ms", config.timeout_ms))?
            .map_err(|e| e.to_string());

        let duration = start_time.elapsed().as_secs_f64();

        // Extract doc_id from event for metrics
        let _doc_id = &envelope.event.doc_id;

        match result {
            Ok(response) => {
                if response.status().is_success() {
                    metrics.record_webhook_request(&config.prefix, "success", duration);
                    info!(
                        "Webhook sent successfully for event {} (channel {}) to prefix '{}'",
                        envelope.event_id, envelope.channel, config.prefix
                    );
                    Ok(())
                } else {
                    let status_code = response.status().as_u16().to_string();
                    metrics.record_webhook_request(&config.prefix, &status_code, duration);
                    let error_msg = format!("Webhook failed with status {}", response.status());
                    error!(
                        "Webhook failed for event {} (channel {}) to prefix '{}': {}",
                        envelope.event_id, envelope.channel, config.prefix, error_msg
                    );
                    Err(error_msg.into())
                }
            }
            Err(e) => {
                metrics.record_webhook_request(&config.prefix, "error", duration);
                Err(e.into())
            }
        }
    }
}

impl EventSender for WebhookSender {
    fn send_event(&self, envelope: EventEnvelope) {
        let matching_prefixes = self.find_matching_prefixes(&envelope.channel);

        for prefix in matching_prefixes {
            if let Some(queue) = self.queues.get(&prefix) {
                if let Err(e) = queue.send(envelope.clone()) {
                    error!("Failed to queue webhook for prefix '{}': {}", prefix, e);
                }
            }
        }
    }

    fn shutdown(&self) {
        debug!(
            "Shutting down WebhookSender with {} workers",
            self.shutdown_senders.len()
        );
        for sender in &self.shutdown_senders {
            let _ = sender.send(()); // Ignore errors if receiver already dropped
        }
    }
}

/// Debounced sync protocol event sender that batches events per user per document
pub struct DebouncedSyncProtocolEventSender {
    inner_sender: Arc<SyncProtocolEventSender>,
    user_queues: Arc<tokio::sync::RwLock<HashMap<String, Arc<UserEventQueue>>>>,
    cleanup_handle: Option<tokio::task::JoinHandle<()>>,
    metrics: Arc<RelayMetrics>,
}

struct UserEventQueue {
    pending_updates: Arc<tokio::sync::Mutex<Vec<Vec<u8>>>>,
    base_event: Arc<tokio::sync::Mutex<Option<EventEnvelope>>>,
    last_sent: Arc<tokio::sync::Mutex<Option<tokio::time::Instant>>>,
    debounce_handle: Arc<tokio::sync::Mutex<Option<tokio::task::JoinHandle<()>>>>,
}

impl UserEventQueue {
    fn new() -> Self {
        Self {
            pending_updates: Arc::new(tokio::sync::Mutex::new(Vec::new())),
            base_event: Arc::new(tokio::sync::Mutex::new(None)),
            last_sent: Arc::new(tokio::sync::Mutex::new(None)),
            debounce_handle: Arc::new(tokio::sync::Mutex::new(None)),
        }
    }

    async fn should_send_immediately(&self) -> bool {
        let last_sent = self.last_sent.lock().await;
        match *last_sent {
            Some(last) => last.elapsed() >= tokio::time::Duration::from_secs(1),
            None => true,
        }
    }

    async fn mark_sent(&self) {
        let mut last_sent = self.last_sent.lock().await;
        *last_sent = Some(tokio::time::Instant::now());
    }

    async fn cancel_pending_task(&self) {
        let mut handle = self.debounce_handle.lock().await;
        if let Some(task) = handle.take() {
            task.abort();
        }
    }
}

impl DebouncedSyncProtocolEventSender {
    pub fn new(inner_sender: Arc<SyncProtocolEventSender>, metrics: Arc<RelayMetrics>) -> Self {
        let user_queues = Arc::new(tokio::sync::RwLock::new(HashMap::new()));
        let cleanup_interval = tokio::time::Duration::from_secs(60);

        // Start cleanup task
        let queues_for_cleanup = user_queues.clone();
        let cleanup_handle = Some(tokio::spawn(async move {
            let mut interval = tokio::time::interval(cleanup_interval);
            loop {
                interval.tick().await;
                Self::cleanup_idle_queues(&queues_for_cleanup).await;
            }
        }));

        Self {
            inner_sender,
            user_queues,
            cleanup_handle,
            metrics,
        }
    }

    /// Create a unique key for the user-document combination
    fn create_queue_key(doc_id: &str, user: Option<&str>) -> String {
        match user {
            Some(u) => format!("{}:{}", doc_id, u),
            None => format!("{}:__anonymous__", doc_id),
        }
    }

    async fn get_or_create_queue(&self, doc_id: &str, user: Option<&str>) -> Arc<UserEventQueue> {
        let key = Self::create_queue_key(doc_id, user);
        let mut queues = self.user_queues.write().await;
        queues
            .entry(key)
            .or_insert_with(|| Arc::new(UserEventQueue::new()))
            .clone()
    }

    async fn cleanup_idle_queues(
        queues: &Arc<tokio::sync::RwLock<HashMap<String, Arc<UserEventQueue>>>>,
    ) {
        let idle_threshold = tokio::time::Duration::from_secs(300); // 5 minutes
        let mut to_remove = Vec::new();

        let queues_guard = queues.read().await;
        for (queue_key, queue) in queues_guard.iter() {
            if let Some(last_sent) = *queue.last_sent.lock().await {
                if last_sent.elapsed() > idle_threshold {
                    to_remove.push(queue_key.clone());
                }
            }
        }
        drop(queues_guard);

        if !to_remove.is_empty() {
            let mut queues_guard = queues.write().await;
            for queue_key in to_remove {
                queues_guard.remove(&queue_key);
                tracing::debug!("Cleaned up idle event queue for {}", queue_key);
            }
        }
    }

    pub async fn queue_event(&self, envelope: EventEnvelope) {
        let doc_id = &envelope.event.doc_id;

        // Extract user from event
        let user = envelope.event.user.as_deref();

        // Get the queue for this specific user-document combination
        let queue = self.get_or_create_queue(doc_id, user).await;

        // Extract update data directly from the event
        let update_data = envelope.event.update.clone();

        // Cancel any existing debounce timer for this user
        queue.cancel_pending_task().await;

        // Store the base event template and add the update to pending updates
        {
            let mut base_event = queue.base_event.lock().await;
            if base_event.is_none() {
                *base_event = Some(envelope.clone());
            }

            if let Some(update) = update_data {
                let mut pending_updates = queue.pending_updates.lock().await;
                pending_updates.push(update);
            }
        }

        // Check if we can send immediately (rate limit allows it)
        if queue.should_send_immediately().await {
            // Merge all pending updates and send immediately
            self.send_merged_event(&queue).await;
            queue.mark_sent().await;
        } else {
            // Schedule debounced send
            let delay = {
                let last_sent = queue.last_sent.lock().await;
                match *last_sent {
                    Some(last) => {
                        let elapsed = last.elapsed();
                        if elapsed < tokio::time::Duration::from_secs(1) {
                            tokio::time::Duration::from_secs(1) - elapsed
                        } else {
                            tokio::time::Duration::from_millis(0)
                        }
                    }
                    None => tokio::time::Duration::from_millis(0),
                }
            };

            let inner_sender = self.inner_sender.clone();
            let queue_clone = queue.clone();
            let metrics = self.metrics.clone();

            let task = tokio::spawn(async move {
                tokio::time::sleep(delay).await;

                // Create a temporary sender to call send_merged_event
                let temp_sender = DebouncedSyncProtocolEventSender {
                    inner_sender,
                    user_queues: Default::default(), // Not used in send_merged_event
                    cleanup_handle: None,
                    metrics,
                };

                temp_sender.send_merged_event(&queue_clone).await;
                queue_clone.mark_sent().await;
            });

            // Store the task handle
            let mut handle = queue.debounce_handle.lock().await;
            *handle = Some(task);
        }
    }

    async fn send_merged_event(&self, queue: &UserEventQueue) {
        let (base_event, updates) = {
            let mut base_event = queue.base_event.lock().await;
            let mut pending_updates = queue.pending_updates.lock().await;

            let event = base_event.take();
            let updates = std::mem::take(&mut *pending_updates);
            (event, updates)
        };

        if let Some(event) = base_event {
            if !updates.is_empty() {
                // Merge all updates using yrs::merge_updates_v1
                match yrs::merge_updates_v1(&updates) {
                    Ok(merged_update) => {
                        // Create a new event with the merged update data
                        let mut new_event = event.clone();
                        new_event.event.update = Some(merged_update);

                        // Record metrics for update merging
                        let _user = new_event.event.user.as_deref().unwrap_or("__anonymous__");
                        self.metrics.record_updates_merged(updates.len());

                        tracing::debug!("Merged {} updates for user-document", updates.len());
                        self.inner_sender.send_event(new_event);
                    }
                    Err(e) => {
                        tracing::warn!("Failed to merge updates: {}, sending latest event", e);
                        self.inner_sender.send_event(event);
                    }
                }
            } else {
                // No updates to merge, send the base event
                self.inner_sender.send_event(event);
            }
        }
    }
}

impl EventSender for DebouncedSyncProtocolEventSender {
    fn send_event(&self, envelope: EventEnvelope) {
        // Clone necessary fields for the async task
        let inner_sender = self.inner_sender.clone();
        let user_queues = self.user_queues.clone();

        let metrics = self.metrics.clone();

        tokio::spawn(async move {
            let temp_sender = DebouncedSyncProtocolEventSender {
                inner_sender,
                user_queues,
                cleanup_handle: None, // Don't clone the cleanup handle
                metrics,
            };
            temp_sender.queue_event(envelope).await;
        });
    }

    fn shutdown(&self) {
        // Cancel cleanup task
        if let Some(handle) = &self.cleanup_handle {
            handle.abort();
        }

        // Shutdown inner sender
        self.inner_sender.shutdown();
    }
}

/// Sync protocol event sender using weak references to avoid circular dependencies
pub struct SyncProtocolEventSender {
    // Map from document ID to list of weak references to DocConnections
    doc_connections:
        Arc<RwLock<HashMap<String, Vec<std::sync::Weak<crate::doc_connection::DocConnection>>>>>,
    metrics: Option<Arc<RelayMetrics>>,
}

impl SyncProtocolEventSender {
    pub fn new() -> Self {
        Self {
            doc_connections: Arc::new(RwLock::new(HashMap::new())),
            metrics: None,
        }
    }

    pub fn with_metrics(mut self, metrics: Arc<RelayMetrics>) -> Self {
        self.metrics = Some(metrics);
        self
    }

    /// Register a DocConnection for a document
    pub fn register_doc_connection(
        &self,
        doc_id: String,
        connection: std::sync::Weak<crate::doc_connection::DocConnection>,
    ) {
        if let Ok(mut connections) = self.doc_connections.write() {
            let doc_connections = connections.entry(doc_id.clone()).or_insert_with(Vec::new);
            doc_connections.push(connection);
            let before_cleanup = doc_connections.len();

            // Clean up any dead weak references while we're here
            doc_connections.retain(|weak_conn| weak_conn.strong_count() > 0);

            let current_doc_connections = doc_connections.len();
            // Live-measured (#3b690117): this retain is a real disconnect-detection
            // path in its own right, not just incidental tidying — a WebSocket that
            // closes without any further document update never reaches send_event's
            // sweep, so a subsequent register_doc_connection on the SAME doc_id is
            // often the only place that dead ref ever gets cleaned up. Skipping the
            // closed-counter credit here would silently undercount closes for
            // exactly the "connect, disconnect, nothing else happens on that doc"
            // shape the counter exists to make visible.
            let closed_here = before_cleanup - current_doc_connections;

            // Update metrics
            if let Some(ref metrics) = self.metrics {
                // Calculate total connections across all documents
                let total_connections: usize = connections.values().map(|v| v.len()).sum();
                metrics.set_sync_protocol_connections(total_connections);
                metrics
                    .set_sync_protocol_subscriptions_by_channel(&doc_id, current_doc_connections);
                metrics.set_docs_active(connections.len());
                metrics.record_doc_connection_opened();
                if closed_here > 0 {
                    metrics.record_doc_connections_closed(closed_here);
                }
            }

            tracing::debug!(
                "Registered DocConnection for document {}. Total connections: {}",
                doc_id,
                current_doc_connections
            );
        }
    }

    /// Remove exactly one disconnected socket without removing its live peers.
    pub fn unregister_doc_connection(
        &self,
        doc_id: &str,
        connection: &std::sync::Weak<crate::doc_connection::DocConnection>,
    ) {
        if let Ok(mut connections) = self.doc_connections.write() {
            let Some(doc_connections) = connections.get_mut(doc_id) else {
                return;
            };
            let before = doc_connections.len();
            doc_connections.retain(|registered| !registered.ptr_eq(connection));
            let remaining = doc_connections.len();
            let removed = before - remaining;
            if remaining == 0 {
                connections.remove(doc_id);
            }
            if let Some(ref metrics) = self.metrics {
                if remaining == 0 {
                    metrics.clear_doc_subscription(doc_id);
                } else {
                    metrics.set_sync_protocol_subscriptions_by_channel(doc_id, remaining);
                }
                metrics.set_sync_protocol_connections(connections.values().map(Vec::len).sum());
                metrics.set_docs_active(connections.len());
                if removed > 0 {
                    metrics.record_doc_connections_closed(removed);
                }
            }
        }
    }

    /// Unregister all connections for a document (called when document is dropped)
    pub fn unregister_document(&self, doc_id: &str) {
        if let Ok(mut connections) = self.doc_connections.write() {
            let removed = connections.remove(doc_id);

            // Update metrics - this document's channel has no subscriptions left
            if let Some(ref metrics) = self.metrics {
                // Remove the series entirely rather than setting it to 0 — see
                // clear_doc_subscription's doc comment for why an unbounded doc_id
                // label must not be left behind forever.
                metrics.clear_doc_subscription(doc_id);

                if let Some(closed_count) = removed.map(|v| v.len()).filter(|&n| n > 0) {
                    metrics.record_doc_connections_closed(closed_count);
                }

                // Update total connections count across all documents
                let total_connections: usize = connections.values().map(|v| v.len()).sum();
                metrics.set_sync_protocol_connections(total_connections);
                metrics.set_docs_active(connections.len());
            }

            tracing::debug!("Unregistered all DocConnections for document {}", doc_id);
        }
    }

    /// Convert DocumentUpdatedEvent to EventMessage
    fn convert_to_event_message(
        &self,
        envelope: &EventEnvelope,
    ) -> Result<EventMessage, Box<dyn std::error::Error>> {
        let timestamp = envelope.timestamp.timestamp_millis() as u64;

        // Extract user from event
        let user = envelope.event.user.clone();

        // Convert metadata to JSON value
        let metadata = if envelope.event.metadata.is_empty() {
            None
        } else {
            Some(serde_json::to_value(&envelope.event.metadata)?)
        };

        Ok(EventMessage {
            event_id: envelope.event_id.clone(),
            event_type: envelope.event_type.clone(),
            doc_id: envelope.event.doc_id.clone(),
            timestamp,
            user,
            metadata,
            update: envelope.event.update.clone(),
        })
    }
}

impl EventSender for SyncProtocolEventSender {
    fn send_event(&self, envelope: EventEnvelope) {
        // Use the channel for routing (where connections are registered)
        // but the EventMessage will contain the correct doc_id from the event
        let routing_key = &envelope.channel;

        if let Ok(connections) = self.doc_connections.read() {
            if let Some(doc_connections) = connections.get(routing_key) {
                // Convert EventEnvelope to EventMessage
                match self.convert_to_event_message(&envelope) {
                    Ok(event_message) => {
                        let mut sent_count = 0;
                        let mut failed_count = 0;

                        for weak_conn in doc_connections {
                            if let Some(connection) = weak_conn.upgrade() {
                                match connection.send_event(&event_message) {
                                    Ok(()) => {
                                        sent_count += 1;
                                        // Record successful delivery
                                        if let Some(ref metrics) = self.metrics {
                                            metrics.record_event_delivered(
                                                &envelope.event_type,
                                                "sync_protocol",
                                            );
                                        }
                                    }
                                    Err(e) => {
                                        tracing::warn!(
                                            "Failed to send event to DocConnection: {}",
                                            e
                                        );
                                        failed_count += 1;
                                    }
                                }
                            }
                        }

                        tracing::debug!(
                            "Sent event {} to {} DocConnections for routing_key {} ({} failed)",
                            event_message.event_id,
                            sent_count,
                            routing_key,
                            failed_count
                        );
                    }
                    Err(e) => {
                        tracing::error!("Failed to convert EventEnvelope to EventMessage: {}", e);
                    }
                }
            } else {
                tracing::debug!(
                    "No DocConnections registered for routing_key {}",
                    routing_key
                );
            }
        } else {
            tracing::warn!("Failed to acquire DocConnections read lock");
        }

        // Clean up dead weak references periodically
        if let Ok(mut connections) = self.doc_connections.write() {
            let mut metrics_updates = Vec::new();
            let mut closed_this_sweep: usize = 0;

            for (doc_id, doc_connections) in connections.iter_mut() {
                let before_count = doc_connections.len();
                doc_connections.retain(|weak_conn| weak_conn.strong_count() > 0);
                let after_count = doc_connections.len();

                // Track if this channel's count changed
                if before_count != after_count {
                    closed_this_sweep += before_count - after_count;
                    metrics_updates.push((doc_id.clone(), after_count));
                }
            }

            // Remove empty document entries and track them for metrics updates
            let mut removed_docs = Vec::new();
            connections.retain(|doc_id, doc_connections| {
                if doc_connections.is_empty() {
                    removed_docs.push(doc_id.clone());
                    false
                } else {
                    true
                }
            });

            // Update metrics if we have changes
            if !metrics_updates.is_empty() || !removed_docs.is_empty() {
                if let Some(ref metrics) = self.metrics {
                    // Update metrics for changed channels. A channel that dropped to
                    // zero connections has its series removed entirely (not set to 0)
                    // — see clear_doc_subscription's doc comment for why an unbounded
                    // doc_id label must not be left behind forever.
                    for (doc_id, count) in metrics_updates {
                        if count == 0 {
                            metrics.clear_doc_subscription(&doc_id);
                        } else {
                            metrics.set_sync_protocol_subscriptions_by_channel(&doc_id, count);
                        }
                    }

                    // Belt-and-suspenders: also clear channels whose key was removed
                    // from the map this sweep (same set as the count==0 branch above
                    // in practice, but cheap and idempotent to cover explicitly).
                    for doc_id in removed_docs {
                        metrics.clear_doc_subscription(&doc_id);
                    }

                    // Update total connections and active-document counts
                    let total_connections: usize = connections.values().map(|v| v.len()).sum();
                    metrics.set_sync_protocol_connections(total_connections);
                    metrics.set_docs_active(connections.len());

                    if closed_this_sweep > 0 {
                        metrics.record_doc_connections_closed(closed_this_sweep);
                    }
                }
            }
        }
    }

    fn shutdown(&self) {
        tracing::debug!("Shutting down SyncProtocolEventSender");
        if let Ok(mut connections) = self.doc_connections.write() {
            connections.clear();
        }
    }
}

#[cfg(test)]
mod debounced_sync_tests {
    use super::*;

    #[test]
    fn test_create_queue_key_with_user() {
        let key = DebouncedSyncProtocolEventSender::create_queue_key("doc123", Some("alice"));
        assert_eq!(key, "doc123:alice");
    }

    #[test]
    fn test_create_queue_key_without_user() {
        let key = DebouncedSyncProtocolEventSender::create_queue_key("doc123", None);
        assert_eq!(key, "doc123:__anonymous__");
    }

    #[tokio::test]
    async fn test_per_user_queuing() {
        let metrics = RelayMetrics::new_for_test().unwrap();
        let sender = DebouncedSyncProtocolEventSender {
            inner_sender: Arc::new(SyncProtocolEventSender::new()),
            user_queues: Arc::new(tokio::sync::RwLock::new(HashMap::new())),
            cleanup_handle: None,
            metrics,
        };

        // Get queues for different users on same document
        let alice_queue = sender.get_or_create_queue("doc1", Some("alice")).await;
        let bob_queue = sender.get_or_create_queue("doc1", Some("bob")).await;
        let anon_queue = sender.get_or_create_queue("doc1", None).await;

        // Should be different queues
        assert!(!Arc::ptr_eq(&alice_queue, &bob_queue));
        assert!(!Arc::ptr_eq(&alice_queue, &anon_queue));
        assert!(!Arc::ptr_eq(&bob_queue, &anon_queue));

        // Same user should get same queue
        let alice_queue2 = sender.get_or_create_queue("doc1", Some("alice")).await;
        assert!(Arc::ptr_eq(&alice_queue, &alice_queue2));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_document_updated_event_creation() {
        let doc_id = "test_doc_123".to_string();
        let event = DocumentUpdatedEvent::new(doc_id.clone());

        assert_eq!(event.doc_id, doc_id);
        assert_eq!(event.user, None);
        assert!(event.metadata.is_empty());
        assert_eq!(DocumentUpdatedEvent::event_type(), "document.updated");
    }

    #[test]
    fn test_document_updated_event_with_user() {
        let doc_id = "test_doc_123".to_string();
        let user = "user@example.com".to_string();
        let event = DocumentUpdatedEvent::new(doc_id.clone()).with_user(user.clone());

        assert_eq!(event.doc_id, doc_id);
        assert_eq!(event.user, Some(user));
        assert!(event.metadata.is_empty());
    }

    #[test]
    fn test_register_unregister_and_sweep_track_docs_active_and_open_close_counters() {
        // Exercises the actual glue code in register_doc_connection/send_event's
        // dead-weak-ref sweep (not just RelayMetrics in isolation) — proves
        // relay_server_docs_active_total, the open/close counters, and the
        // cardinality-safe removal of the per-doc_id series all follow a real
        // connect -> disconnect -> sweep lifecycle correctly.
        use crate::api_types::Authorization;
        use crate::doc_connection::DocConnection;
        use prometheus::core::Collector;

        let metrics = RelayMetrics::new_for_test().unwrap();
        let sender = SyncProtocolEventSender::new().with_metrics(metrics.clone());

        let doc_id = "doc-lifecycle-test".to_string();
        let awareness = Arc::new(RwLock::new(crate::sync::awareness::Awareness::new(
            yrs::Doc::new(),
        )));
        let connection = Arc::new(DocConnection::new(awareness, Authorization::Full, |_| {}));

        sender.register_doc_connection(doc_id.clone(), Arc::downgrade(&connection));

        assert_eq!(metrics.docs_active.with_label_values(&[]).get(), 1.0);
        assert_eq!(
            metrics
                .doc_connections_opened_total
                .with_label_values(&[])
                .get(),
            1.0
        );
        assert_eq!(
            metrics.sync_protocol_connections.with_label_values(&[]).get(),
            1.0
        );
        assert_eq!(
            metrics
                .sync_protocol_subscriptions_by_channel
                .with_label_values(&[doc_id.as_str()])
                .get(),
            1.0
        );

        // Drop the only strong reference — the weak ref registered above can no
        // longer upgrade, simulating a real disconnect.
        drop(connection);

        // send_event's dead-weak-ref sweep is what actually detects a disconnect in
        // this codebase (there is no Drop hook on DocConnection wired to metrics) —
        // fire an event on the same channel to trigger that cleanup pass.
        let event = DocumentUpdatedEvent::new(doc_id.clone());
        let envelope = EventEnvelope::new(doc_id.clone(), event);
        sender.send_event(envelope);

        assert_eq!(metrics.docs_active.with_label_values(&[]).get(), 0.0);
        assert_eq!(
            metrics
                .doc_connections_closed_total
                .with_label_values(&[])
                .get(),
            1.0
        );
        assert_eq!(
            metrics.sync_protocol_connections.with_label_values(&[]).get(),
            0.0
        );

        let series_exists = metrics
            .sync_protocol_subscriptions_by_channel
            .collect()
            .iter()
            .flat_map(|f| f.get_metric())
            .any(|m| m.get_label().iter().any(|l| l.get_value() == doc_id));
        assert!(
            !series_exists,
            "doc series should be fully removed after disconnect, not left at 0"
        );
    }

    #[test]
    fn test_register_credits_closed_counter_for_dead_refs_it_cleans_up() {
        // Regression test for a gap found during live verification (#3b690117): a
        // disconnect that is only ever detected by register_doc_connection's OWN
        // retain() (because no further document event ever triggers send_event's
        // sweep) must still be credited to doc_connections_closed_total — otherwise
        // exactly the "connect, disconnect, nothing else happens on that doc" shape
        // silently undercounts, which is the shape the counter most needs to catch.
        use crate::api_types::Authorization;
        use crate::doc_connection::DocConnection;

        let metrics = RelayMetrics::new_for_test().unwrap();
        let sender = SyncProtocolEventSender::new().with_metrics(metrics.clone());
        let doc_id = "doc-retain-closes-test".to_string();

        let awareness = Arc::new(RwLock::new(crate::sync::awareness::Awareness::new(
            yrs::Doc::new(),
        )));
        let conn_a = Arc::new(DocConnection::new(
            awareness.clone(),
            Authorization::Full,
            |_| {},
        ));
        sender.register_doc_connection(doc_id.clone(), Arc::downgrade(&conn_a));
        drop(conn_a);

        // No send_event call here — this is the point of the test: the ONLY thing
        // that can detect conn_a's death before conn_b registers is the retain()
        // inside register_doc_connection itself.
        let conn_b = Arc::new(DocConnection::new(awareness, Authorization::Full, |_| {}));
        sender.register_doc_connection(doc_id.clone(), Arc::downgrade(&conn_b));

        assert_eq!(
            metrics
                .doc_connections_opened_total
                .with_label_values(&[])
                .get(),
            2.0
        );
        assert_eq!(
            metrics
                .doc_connections_closed_total
                .with_label_values(&[])
                .get(),
            1.0,
            "conn_a's death, detected only via register's own retain(), must still be credited"
        );
        // Exactly conn_b remains — the doc itself stays active, just with fewer
        // connections, so this is a set (not a remove) on the per-channel series.
        assert_eq!(
            metrics
                .sync_protocol_subscriptions_by_channel
                .with_label_values(&[doc_id.as_str()])
                .get(),
            1.0
        );
        assert_eq!(metrics.docs_active.with_label_values(&[]).get(), 1.0);
    }

    #[test]
    fn test_event_envelope_creation() {
        let doc_id = "test_doc_123".to_string();
        let event = DocumentUpdatedEvent::new(doc_id.clone());
        let channel = doc_id.clone();

        let envelope = EventEnvelope::new(channel.clone(), event);

        assert_eq!(envelope.channel, channel);
        assert_eq!(envelope.event_type, "document.updated");
        assert!(envelope.event_id.starts_with("evt_"));
        assert_eq!(envelope.event_id.len(), 25); // "evt_" + 21 chars

        // Check event structure
        assert_eq!(envelope.event.doc_id, doc_id);
        assert_eq!(envelope.event.user, None);
        assert!(envelope.event.metadata.is_empty());
    }

    #[test]
    fn test_server_message_from_envelope() {
        let doc_id = "test_doc_123".to_string();
        let event = DocumentUpdatedEvent::new(doc_id.clone());
        let envelope = EventEnvelope::new(doc_id.clone(), event);

        let message: ServerMessage = envelope.clone().into();

        match message {
            ServerMessage::Event {
                event_type,
                event_id,
                channel,
                timestamp: _,
                payload,
            } => {
                assert_eq!(event_type, "document.updated");
                assert_eq!(event_id, envelope.event_id);
                assert_eq!(channel, envelope.channel);
                assert_eq!(payload["doc_id"], doc_id);
                assert_eq!(
                    payload["metadata"],
                    serde_json::Value::Object(serde_json::Map::new())
                );
            }
            _ => panic!("Expected Event message"),
        }
    }

    #[test]
    fn test_webhook_payload_from_envelope() {
        let doc_id = "test_doc_123".to_string();
        let event = DocumentUpdatedEvent::new(doc_id.clone());
        let envelope = EventEnvelope::new(doc_id.clone(), event);

        let payload: WebhookPayload = envelope.clone().into();

        assert_eq!(payload.event_type, "document.updated");
        assert_eq!(payload.event_id, envelope.event_id);

        // Check payload structure
        let payload_obj = payload.payload.as_object().unwrap();
        assert_eq!(payload_obj["doc_id"], doc_id);
        assert_eq!(
            payload_obj["metadata"],
            serde_json::Value::Object(serde_json::Map::new())
        );
    }

    #[tokio::test]
    async fn test_unified_event_dispatcher() {
        // Create mock event senders
        struct MockEventSender {
            envelopes: Arc<RwLock<Vec<EventEnvelope>>>,
        }

        impl EventSender for MockEventSender {
            fn send_event(&self, envelope: EventEnvelope) {
                self.envelopes.write().unwrap().push(envelope);
            }

            fn shutdown(&self) {}
        }

        let sender1_envelopes = Arc::new(RwLock::new(Vec::new()));
        let sender2_envelopes = Arc::new(RwLock::new(Vec::new()));

        let sender1 = Arc::new(MockEventSender {
            envelopes: sender1_envelopes.clone(),
        });
        let sender2 = Arc::new(MockEventSender {
            envelopes: sender2_envelopes.clone(),
        });

        let metrics = RelayMetrics::new_for_test().unwrap();
        let dispatcher = UnifiedEventDispatcher::new(vec![sender1, sender2], metrics);

        let event = DocumentUpdatedEvent::new("test_doc".to_string());
        let envelope = EventEnvelope::new("test_doc".to_string(), event);
        dispatcher.send_event(envelope.clone());

        // Both senders should have received the envelope
        assert_eq!(sender1_envelopes.read().unwrap().len(), 1);
        assert_eq!(sender2_envelopes.read().unwrap().len(), 1);
        assert_eq!(
            sender1_envelopes.read().unwrap()[0].channel,
            envelope.channel
        );
        assert_eq!(
            sender2_envelopes.read().unwrap()[0].channel,
            envelope.channel
        );
    }

    #[tokio::test]
    async fn test_webhook_sender_prefix_matching() {
        use crate::webhook::WebhookConfig;

        let configs = vec![
            WebhookConfig {
                prefix: "user_".to_string(),
                url: "https://example.com/user".to_string(),
                timeout_ms: 5000,
                auth_token: None,
            },
            WebhookConfig {
                prefix: "admin_".to_string(),
                url: "https://example.com/admin".to_string(),
                timeout_ms: 5000,
                auth_token: None,
            },
        ];

        let metrics = RelayMetrics::new_for_test().unwrap();
        let sender = WebhookSender::new(configs, metrics).unwrap();

        // Test prefix matching
        let matches = sender.find_matching_prefixes("user_alice_doc");
        assert_eq!(matches, vec!["user_"]);

        let matches = sender.find_matching_prefixes("admin_settings");
        assert_eq!(matches, vec!["admin_"]);

        let matches = sender.find_matching_prefixes("public_doc");
        assert!(matches.is_empty());
    }

    #[test]
    fn test_webhook_payload_serialization() {
        let event = DocumentUpdatedEvent::new("test_doc".to_string());
        let envelope = EventEnvelope::new("test_doc".to_string(), event);

        let payload: WebhookPayload = envelope.into();
        let json = serde_json::to_string(&payload).unwrap();

        // Verify JSON structure
        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed["eventType"], "document.updated");
        assert!(parsed["eventId"].as_str().unwrap().starts_with("evt_"));
        assert_eq!(parsed["payload"]["doc_id"], "test_doc");
        assert_eq!(
            parsed["payload"]["metadata"],
            serde_json::Value::Object(serde_json::Map::new())
        );
    }

    #[test]
    fn test_cbor_to_json_conversion() {
        use std::collections::BTreeMap;

        // Create CBOR metadata
        let mut cbor_metadata = BTreeMap::new();
        cbor_metadata.insert(
            "channel".to_string(),
            ciborium::value::Value::Text("test-channel".to_string()),
        );
        cbor_metadata.insert(
            "max_users".to_string(),
            ciborium::value::Value::Integer(10.into()),
        );
        cbor_metadata.insert("is_active".to_string(), ciborium::value::Value::Bool(true));

        // Convert to JSON
        let json_result = cbor_metadata_to_json(&cbor_metadata).unwrap();

        assert_eq!(json_result.len(), 3);
        assert_eq!(
            json_result["channel"],
            serde_json::Value::String("test-channel".to_string())
        );
        assert_eq!(
            json_result["max_users"],
            serde_json::Value::Number(serde_json::Number::from(10))
        );
        assert_eq!(json_result["is_active"], serde_json::Value::Bool(true));
    }

    #[test]
    fn test_document_event_with_metadata_method() {
        use crate::store::Store;
        use async_trait::async_trait;
        use dashmap::DashMap;
        use std::sync::Arc;

        #[derive(Default, Clone)]
        struct TestStore {
            data: Arc<DashMap<String, Vec<u8>>>,
        }

        #[cfg_attr(not(feature = "single-threaded"), async_trait)]
        #[cfg_attr(feature = "single-threaded", async_trait(?Send))]
        impl Store for TestStore {
            async fn init(&self) -> crate::store::Result<()> {
                Ok(())
            }
            async fn get(&self, key: &str) -> crate::store::Result<Option<Vec<u8>>> {
                Ok(self.data.get(key).map(|v| v.clone()))
            }
            async fn set(&self, key: &str, value: Vec<u8>) -> crate::store::Result<()> {
                self.data.insert(key.to_owned(), value);
                Ok(())
            }
            async fn remove(&self, key: &str) -> crate::store::Result<()> {
                self.data.remove(key);
                Ok(())
            }
            async fn exists(&self, key: &str) -> crate::store::Result<bool> {
                Ok(self.data.contains_key(key))
            }
        }

        // This test should use a runtime to be async, but for now we'll test the sync parts
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let store = TestStore::default();
            let sync_kv =
                crate::sync_kv::SyncKv::new(Some(Arc::new(Box::new(store))), "test_doc", || ())
                    .await
                    .unwrap();

            // Add metadata to sync_kv
            sync_kv.update_metadata(
                "doc_type".to_string(),
                ciborium::value::Value::Text("collaborative".to_string()),
            );
            sync_kv.update_metadata(
                "version".to_string(),
                ciborium::value::Value::Integer(2.into()),
            );

            // Create event with metadata
            let event = DocumentUpdatedEvent::new("test_doc".to_string()).with_metadata(&sync_kv);

            // Verify metadata was included
            assert_eq!(event.metadata.len(), 2);
            assert_eq!(
                event.metadata["doc_type"],
                serde_json::Value::String("collaborative".to_string())
            );
            assert_eq!(
                event.metadata["version"],
                serde_json::Value::Number(serde_json::Number::from(2))
            );
        });
    }

    #[test]
    fn test_with_metadata_excludes_subdocs_index() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let sync_kv = crate::sync_kv::SyncKv::new(None, "test_doc", || ())
                .await
                .unwrap();

            sync_kv.update_metadata(
                "channel".to_string(),
                ciborium::value::Value::Text("folder-1".to_string()),
            );
            sync_kv.update_metadata(
                "subdocs".to_string(),
                ciborium::value::Value::Map(vec![(
                    ciborium::value::Value::Text("subdoc-abc".to_string()),
                    ciborium::value::Value::Map(vec![(
                        ciborium::value::Value::Text("snapshot".to_string()),
                        ciborium::value::Value::Bytes(vec![1, 2, 3]),
                    )]),
                )]),
            );

            let event = DocumentUpdatedEvent::new("test_doc".to_string()).with_metadata(&sync_kv);

            assert_eq!(
                event.metadata["channel"],
                serde_json::Value::String("folder-1".to_string())
            );
            assert!(
                !event.metadata.contains_key("subdocs"),
                "subdocs index must not leak into event payloads"
            );
        });
    }
}
