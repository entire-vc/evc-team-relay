use anyhow::{anyhow, Result};
use axum::{
    body::Bytes,
    extract::DefaultBodyLimit,
    extract::{
        multipart::Multipart,
        ws::{CloseFrame, Message, WebSocket},
        MatchedPath, Path, Query, Request, State, WebSocketUpgrade,
    },
    http::{
        header::{HeaderName, HeaderValue},
        StatusCode,
    },
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{delete, get, head, post},
    Json, Router,
};
use axum_extra::typed_header::TypedHeader;
use dashmap::{mapref::one::MappedRef, DashMap};
use futures::{SinkExt, StreamExt, TryStreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    io::Write,
    sync::{Arc, RwLock},
    time::Duration,
};
use tempfile::NamedTempFile;
use tokio::{
    net::TcpListener,
    sync::{
        mpsc::{channel, Receiver},
        Mutex as AsyncMutex,
    },
};
use tokio_util::{sync::CancellationToken, task::TaskTracker};
use tracing::{span, Instrument, Level};
use url::Url;
use y_sweet_core::{
    api_types::{
        validate_doc_name, validate_file_hash, AuthDocRequest, Authorization, ClientToken,
        DocCreationRequest, DocumentVersionEntry, DocumentVersionResponse, FileDownloadUrlResponse,
        FileHistoryEntry, FileHistoryResponse, FileUploadUrlResponse, NewDocResponse,
    },
    auth::{Authenticator, ExpirationTimeEpochMillis, Permission, DEFAULT_EXPIRATION_SECONDS},
    doc_connection::DocConnection,
    doc_sync::DocWithSyncKv,
    event::{
        DebouncedSyncProtocolEventSender, DocumentUpdatedEvent, EventDispatcher, EventEnvelope,
        EventSender, SyncProtocolEventSender, UnifiedEventDispatcher, WebhookSender,
    },
    metrics::RelayMetrics,
    store::Store,
    sync::awareness::Awareness,
    sync_kv::SyncKv,
    webhook::WebhookConfig,
};

const RELAY_SERVER_VERSION: &str = env!("GIT_VERSION");

#[derive(Clone, Debug)]
pub struct AllowedHost {
    pub host: String,
    pub scheme: String, // "http" or "https"
}

fn current_time_epoch_millis() -> u64 {
    let now = std::time::SystemTime::now();
    let duration_since_epoch = now.duration_since(std::time::UNIX_EPOCH).unwrap();
    duration_since_epoch.as_millis() as u64
}

async fn auth_metrics_middleware(
    State(server_state): State<Arc<Server>>,
    matched_path: Option<MatchedPath>,
    req: Request,
    next: Next,
) -> Response {
    let method = req.method().to_string();
    let resp = next.run(req).await;
    let status = resp.status();

    let path = matched_path
        .as_ref()
        .map(|m| m.as_str())
        .unwrap_or("unknown");

    if status == StatusCode::UNAUTHORIZED || status == StatusCode::FORBIDDEN {
        let error_type = resp
            .extensions()
            .get::<AuthErrorType>()
            .map(|e| e.0)
            .unwrap_or("unknown");
        let status_str = status.as_u16().to_string();

        server_state
            .metrics
            .record_http_auth_error(error_type, &status_str, path, &method);
    } else if status.is_success() || status == StatusCode::SWITCHING_PROTOCOLS {
        // The success-side counterpart to the error branch above, sharing its
        // `path`/`method` labels — without it, "N auth errors" has no denominator
        // to compute a success rate from (see relay_server_http_auth_success_total
        // doc comment in metrics.rs). 101 Switching Protocols is the success status
        // for the WS-upgrade endpoints, which don't return a 2xx.
        server_state.metrics.record_http_auth_success(path, &method);
    }

    resp
}

fn validate_file_token(
    server_state: &Arc<Server>,
    token: &str,
    doc_id: &str,
) -> Result<Permission, AppError> {
    let authenticator = server_state.authenticator.as_ref().ok_or_else(|| {
        AppError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            anyhow!("No authenticator configured"),
        )
    })?;

    let permission = authenticator
        .verify_token_auto(token, current_time_epoch_millis())
        .map_err(|auth_error| {
            AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("Invalid token"),
                auth_error.to_metric_label(),
            )
        })?;

    match &permission {
        Permission::File(file_permission) => {
            if file_permission.doc_id != doc_id {
                return Err(AppError::auth(
                    StatusCode::UNAUTHORIZED,
                    anyhow!("Token not valid for this document"),
                    "access_wrong_document",
                ));
            }
        }
        _ => {
            return Err(AppError::auth(
                StatusCode::BAD_REQUEST,
                anyhow!("Token must be a file token"),
                "wrong_token_type",
            ));
        }
    }

    Ok(permission)
}

/// Newtype for passing auth error context through response extensions.
#[derive(Clone, Debug)]
pub struct AuthErrorType(pub &'static str);

#[derive(Debug)]
pub struct AppError {
    pub status: StatusCode,
    pub error: anyhow::Error,
    auth_error_type: Option<&'static str>,
}

impl AppError {
    fn new(status: StatusCode, error: anyhow::Error) -> Self {
        Self {
            status,
            error,
            auth_error_type: None,
        }
    }

    pub fn auth(status: StatusCode, error: anyhow::Error, error_type: &'static str) -> Self {
        Self {
            status,
            error,
            auth_error_type: Some(error_type),
        }
    }
}

impl std::error::Error for AppError {}
impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let mut response =
            (self.status, format!("Something went wrong: {}", self.error)).into_response();
        if let Some(error_type) = self.auth_error_type {
            response.extensions_mut().insert(AuthErrorType(error_type));
        }
        response
    }
}
impl<E> From<(StatusCode, E)> for AppError
where
    E: Into<anyhow::Error>,
{
    fn from((status_code, err): (StatusCode, E)) -> Self {
        Self {
            status: status_code,
            error: err.into(),
            auth_error_type: None,
        }
    }
}
impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Status code: {} {}", self.status, self.error)?;
        Ok(())
    }
}

#[derive(Deserialize)]
struct FileDownloadQueryParams {
    hash: Option<String>,
}

#[derive(Deserialize)]
struct FileUploadParams {
    token: String,
}

#[derive(Deserialize)]
struct FileDownloadParams {
    token: String,
    hash: String,
}

pub struct Server {
    docs: Arc<DashMap<String, DocWithSyncKv>>,
    /// Per-doc-id async locks held only across the load-from-store step
    /// so concurrent first-time requests for the same doc don't both load
    /// from the store and clobber each other in `docs`.
    loading_locks: Arc<DashMap<String, Arc<AsyncMutex<()>>>>,
    doc_worker_tracker: TaskTracker,
    store: Option<Arc<Box<dyn Store>>>,
    checkpoint_freq: Duration,
    authenticator: Option<Authenticator>,
    url: Option<Url>,
    allowed_hosts: Vec<AllowedHost>,
    cancellation_token: CancellationToken,
    /// Whether to garbage collect docs that are no longer in use.
    doc_gc: bool,
    event_dispatcher: Option<Arc<dyn EventDispatcher>>,
    sync_protocol_event_sender: Arc<SyncProtocolEventSender>,
    metrics: Arc<RelayMetrics>,
}

impl Server {
    pub async fn new(
        store: Option<Box<dyn Store>>,
        checkpoint_freq: Duration,
        authenticator: Option<Authenticator>,
        url: Option<Url>,
        allowed_hosts: Vec<AllowedHost>,
        cancellation_token: CancellationToken,
        doc_gc: bool,
        webhook_configs: Option<Vec<WebhookConfig>>,
    ) -> Result<Self> {
        // Initialize metrics early so all senders can use them
        let metrics = RelayMetrics::new()
            .map_err(|e| anyhow!("Failed to initialize webhook metrics: {}", e))?;

        let sync_protocol_event_sender =
            Arc::new(SyncProtocolEventSender::new().with_metrics(metrics.clone()));

        let debounced_sync_sender = Arc::new(DebouncedSyncProtocolEventSender::new(
            sync_protocol_event_sender.clone(),
            metrics.clone(),
        ));

        let event_dispatcher = if let Some(configs) = webhook_configs {
            let webhook_sender = Arc::new(
                WebhookSender::new(configs.clone(), metrics.clone())
                    .map_err(|e| anyhow!("Failed to create webhook sender: {}", e))?,
            );

            let senders: Vec<Arc<dyn EventSender>> =
                vec![webhook_sender, debounced_sync_sender.clone()];

            Some(
                Arc::new(UnifiedEventDispatcher::new(senders, metrics.clone()))
                    as Arc<dyn EventDispatcher>,
            )
        } else {
            tracing::info!(
                "No webhook configs provided, creating sync protocol-only event dispatcher"
            );
            let senders: Vec<Arc<dyn EventSender>> = vec![debounced_sync_sender.clone()];
            Some(
                Arc::new(UnifiedEventDispatcher::new(senders, metrics.clone()))
                    as Arc<dyn EventDispatcher>,
            )
        };

        tracing::info!("Event dispatcher created successfully");

        Ok(Self {
            docs: Arc::new(DashMap::new()),
            loading_locks: Arc::new(DashMap::new()),
            doc_worker_tracker: TaskTracker::new(),
            store: store.map(Arc::new),
            checkpoint_freq,
            authenticator,
            url,
            allowed_hosts,
            cancellation_token,
            doc_gc,
            event_dispatcher,
            sync_protocol_event_sender,
            metrics,
        })
    }

    pub async fn doc_exists(&self, doc_id: &str) -> bool {
        // Reject system keys
        if Self::validate_doc_id(doc_id).is_err() {
            return false;
        }
        if self.docs.contains_key(doc_id) {
            return true;
        }
        if let Some(store) = &self.store {
            store
                .exists(&format!("{}/data.ysweet", doc_id))
                .await
                .unwrap_or_default()
        } else {
            false
        }
    }

    pub async fn create_doc(&self) -> Result<String> {
        let doc_id = nanoid::nanoid!();
        self.load_doc(&doc_id, None).await?;
        tracing::info!(doc_id=?doc_id, "Created doc");
        Ok(doc_id)
    }

    pub async fn reload_webhook_config(&self) -> Result<String, anyhow::Error> {
        // For now, webhook configuration reloading is not supported with the new event system
        // This would require a more complex architecture to hot-reload the event dispatcher
        // In the meantime, server restart is required to change webhook configuration
        Err(anyhow::anyhow!(
            "Webhook configuration reloading is not yet supported with the new event system. Please restart the server to load new configuration."
        ))
    }

    fn validate_doc_id(doc_id: &str) -> Result<()> {
        // Reject system configuration paths that are reserved for internal use
        if doc_id.starts_with(".config/") || doc_id == ".config" {
            return Err(anyhow::anyhow!(
                "Document ID cannot access system configuration directory '.config'"
            ));
        }
        Ok(())
    }

    pub fn load_doc<'a>(
        &'a self,
        doc_id: &'a str,
        routing_channel: Option<String>,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<()>> + Send + 'a>> {
        Box::pin(self.load_doc_with_user(doc_id, routing_channel, None))
    }

    pub async fn load_doc_with_user(
        &self,
        doc_id: &str,
        routing_channel: Option<String>,
        user: Option<String>,
    ) -> Result<()> {
        Self::validate_doc_id(doc_id)?;
        let (send, recv) = channel(1024);

        // Determine routing channel: use provided channel or fallback to doc_id
        let routing_channel_name = routing_channel
            .clone()
            .unwrap_or_else(|| doc_id.to_string());

        // If this doc routes to a different channel (i.e., it's a subdoc),
        // ensure the parent is loaded and hold a reference to prevent GC.
        // The load goes through the per-key lock: concurrent subdoc loads
        // (e.g. a folder's documents reconnecting together) otherwise race
        // to load the same parent, and the losing insert orphans the
        // winner's in-memory state and spawns duplicate persistence workers.
        let parent_awareness_guard = if routing_channel_name != doc_id {
            self.ensure_doc_loaded_boxed(&routing_channel_name).await?;
            self.docs
                .get(&routing_channel_name)
                .map(|parent| parent.awareness())
        } else {
            None
        };

        // Create event callback with the determined routing channel and user
        let event_callback = {
            let event_dispatcher = self.event_dispatcher.clone();
            let routing_channel_for_callback = routing_channel_name.clone();
            let user_for_callback = user.clone();
            let docs = self.docs.clone();
            let doc_id_for_callback = doc_id.to_string();
            // Capture parent awareness to keep it alive (prevents GC while subdoc exists)
            let _parent_awareness = parent_awareness_guard;

            if let Some(dispatcher) = event_dispatcher {
                Some(Arc::new(move |mut event: DocumentUpdatedEvent| {
                    // Keep parent awareness alive by referencing it in the closure
                    let _ = &_parent_awareness;
                    // Add user to event if available
                    if let Some(ref user) = user_for_callback {
                        event.user = Some(user.clone());
                    }

                    // Update parent's subdoc snapshot index
                    if routing_channel_for_callback != doc_id_for_callback {
                        if let Some(snapshot) = &event.snapshot {
                            if let Some(parent) = docs.get(&routing_channel_for_callback) {
                                parent
                                    .update_subdoc_snapshot(&doc_id_for_callback, snapshot.clone());
                            }
                        }
                    }

                    // Log the full event payload as JSON after user assignment
                    match serde_json::to_string(&event) {
                        Ok(json_str) => {
                            tracing::info!("Document updated event dispatched: {}", json_str);
                        }
                        Err(e) => {
                            tracing::info!(
                                "Document updated event dispatched for doc_id: {} (JSON serialization failed: {})",
                                event.doc_id, e
                            );
                        }
                    }

                    // Step 1: Create the envelope with predetermined routing channel
                    let envelope = EventEnvelope::new(routing_channel_for_callback.clone(), event);

                    // Step 2: Send via dispatcher
                    dispatcher.send_event(envelope);
                }) as y_sweet_core::webhook::WebhookCallback)
            } else {
                None
            }
        };

        let doc_id_for_dirty_callback = doc_id.to_string();
        let dwskv = DocWithSyncKv::new(
            doc_id,
            self.store.clone(),
            move || {
                // Full: persist worker is lagging, but a signal is already
                // queued so the wake-up still happens. Closed: the worker (and
                // likely the doc) is already gone. Neither case is recoverable
                // or needs a second signal — drop instead of panicking (TR-33).
                if let Err(e) = send.try_send(()) {
                    tracing::debug!(doc_id = %doc_id_for_dirty_callback, ?e, "dirty-signal channel try_send failed");
                }
            },
            event_callback,
        )
        .await?;

        // If channel is provided in token, store it in document metadata
        if let Some(channel_name) = routing_channel {
            dwskv.set_channel(&channel_name);
        }

        dwskv
            .sync_kv()
            .persist()
            .await
            .map_err(|e| anyhow!("Error persisting: {:?}", e))?;

        {
            let sync_kv = dwskv.sync_kv();
            let checkpoint_freq = self.checkpoint_freq;
            let doc_id = doc_id.to_string();
            let cancellation_token = self.cancellation_token.clone();

            // Spawn a task to save the document to the store when it changes.
            self.doc_worker_tracker.spawn(
                Self::doc_persistence_worker(
                    recv,
                    sync_kv,
                    checkpoint_freq,
                    doc_id.clone(),
                    cancellation_token.clone(),
                )
                .instrument(span!(Level::INFO, "save_loop", doc_id=?doc_id)),
            );

            if self.doc_gc {
                self.doc_worker_tracker.spawn(
                    Self::doc_gc_worker(
                        self.docs.clone(),
                        doc_id.clone(),
                        checkpoint_freq,
                        cancellation_token,
                    )
                    .instrument(span!(Level::INFO, "gc_loop", doc_id=?doc_id)),
                );
            }
        }

        self.docs.insert(doc_id.to_string(), dwskv);
        Ok(())
    }

    async fn doc_gc_worker(
        docs: Arc<DashMap<String, DocWithSyncKv>>,
        doc_id: String,
        checkpoint_freq: Duration,
        cancellation_token: CancellationToken,
    ) {
        let mut checkpoints_without_refs = 0;

        loop {
            tokio::select! {
                _ = tokio::time::sleep(checkpoint_freq) => {
                    if let Some(doc) = docs.get(&doc_id) {
                        let awareness = Arc::downgrade(&doc.awareness());
                        if awareness.strong_count() > 1 {
                            checkpoints_without_refs = 0;
                            tracing::debug!("doc is still alive - it has {} references", awareness.strong_count());
                        } else {
                            checkpoints_without_refs += 1;
                            tracing::info!("doc has only one reference, candidate for GC. checkpoints_without_refs: {}", checkpoints_without_refs);
                        }
                    } else {
                        break;
                    }

                    if checkpoints_without_refs >= 2 {
                        tracing::info!("GCing doc");
                        if let Some(doc) = docs.get(&doc_id) {
                            // Compact PUD before shutdown: dedup ids, clear ds.
                            // The mutations create tombstones which yrs GC will
                            // clean up, and the update observer marks SyncKv
                            // dirty so the compacted state gets persisted.
                            let result = doc.compact_user_data();
                            if !result.is_empty() {
                                tracing::debug!(
                                    ids_removed = result.ids_removed,
                                    ds_removed = result.ds_removed,
                                    "Compacted PermanentUserData"
                                );
                            }
                            doc.sync_kv().shutdown();
                        }
                        docs.remove(&doc_id);
                        break;
                    }
                }
                _ = cancellation_token.cancelled() => {
                    break;
                }
            };
        }
        tracing::info!("Exiting gc_loop");
    }

    async fn doc_persistence_worker(
        mut recv: Receiver<()>,
        sync_kv: Arc<SyncKv>,
        checkpoint_freq: Duration,
        doc_id: String,
        cancellation_token: CancellationToken,
    ) {
        let mut last_save = std::time::Instant::now();

        loop {
            let is_done = tokio::select! {
                v = recv.recv() => v.is_none(),
                _ = cancellation_token.cancelled() => true,
                _ = tokio::time::sleep(checkpoint_freq) => {
                    sync_kv.is_shutdown()
                }
            };

            tracing::debug!("Received signal. done: {}", is_done);
            let now = std::time::Instant::now();
            if !is_done && now - last_save < checkpoint_freq {
                let sleep = tokio::time::sleep(checkpoint_freq - (now - last_save));
                tokio::pin!(sleep);
                tracing::info!("Throttling.");

                loop {
                    tokio::select! {
                        _ = &mut sleep => {
                            break;
                        }
                        v = recv.recv() => {
                            tracing::info!("Received dirty while throttling.");
                            if v.is_none() {
                                break;
                            }
                        }
                        _ = cancellation_token.cancelled() => {
                            tracing::info!("Received cancellation while throttling.");
                            break;
                        }

                    }
                    tracing::info!("Done throttling.");
                }
            }
            tracing::debug!("Persisting.");
            if let Err(e) = sync_kv.persist().await {
                tracing::error!(?e, "Error persisting.");
            } else {
                tracing::debug!("Done persisting.");
            }
            last_save = std::time::Instant::now();

            if is_done {
                break;
            }
        }
        tracing::info!("Terminating loop for {}", doc_id);
    }

    pub async fn get_or_create_doc(
        &self,
        doc_id: &str,
    ) -> Result<MappedRef<'_, String, DocWithSyncKv, DocWithSyncKv>> {
        self.ensure_doc_loaded(doc_id, None, None).await?;

        Ok(self
            .docs
            .get(doc_id)
            .ok_or_else(|| anyhow!("Failed to get-or-create doc"))?
            .map(|d| d))
    }

    pub async fn get_or_create_doc_with_channel(
        &self,
        doc_id: &str,
        routing_channel: Option<String>,
    ) -> Result<MappedRef<'_, String, DocWithSyncKv, DocWithSyncKv>> {
        self.get_or_create_doc_with_channel_and_user(doc_id, routing_channel, None)
            .await
    }

    pub async fn get_or_create_doc_with_channel_and_user(
        &self,
        doc_id: &str,
        routing_channel: Option<String>,
        user: Option<String>,
    ) -> Result<MappedRef<'_, String, DocWithSyncKv, DocWithSyncKv>> {
        self.ensure_doc_loaded(doc_id, routing_channel, user)
            .await?;

        Ok(self
            .docs
            .get(doc_id)
            .ok_or_else(|| anyhow!("Failed to get-or-create doc"))?
            .map(|d| d))
    }

    /// Idempotently load `doc_id` into `self.docs`. Concurrent callers for
    /// the same doc_id serialize through a per-key async mutex so that only
    /// one `load_doc` actually hits the store; the others observe the
    /// already-loaded entry on a double-check after the lock is acquired.
    async fn ensure_doc_loaded(
        &self,
        doc_id: &str,
        routing_channel: Option<String>,
        user: Option<String>,
    ) -> Result<()> {
        if self.docs.contains_key(doc_id) {
            return Ok(());
        }

        // Acquire (or create) the per-key lock without holding the DashMap
        // shard guard across the `.lock().await` below.
        let lock = {
            let entry = self
                .loading_locks
                .entry(doc_id.to_string())
                .or_insert_with(|| Arc::new(AsyncMutex::new(())));
            Arc::clone(entry.value())
        };
        let _guard = lock.lock().await;

        // Double-check after acquiring the lock: another caller may have
        // loaded while we were waiting.
        if self.docs.contains_key(doc_id) {
            return Ok(());
        }

        tracing::info!(doc_id=?doc_id, channel=?routing_channel, user=?user, "Loading doc");
        self.load_doc_with_user(doc_id, routing_channel, user)
            .await?;
        Ok(())
    }

    /// Boxed form of [`Self::ensure_doc_loaded`] for use inside
    /// `load_doc_with_user`, which it recursively calls to load a subdoc's
    /// parent. The recursion terminates because a parent never routes to
    /// another channel, and no lock cycle exists because parent loads take
    /// only the parent's own key lock.
    fn ensure_doc_loaded_boxed<'a>(
        &'a self,
        doc_id: &'a str,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<()>> + Send + 'a>> {
        Box::pin(self.ensure_doc_loaded(doc_id, None, None))
    }

    pub fn check_auth(
        &self,
        auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
    ) -> Result<(), AppError> {
        if let Some(auth) = &self.authenticator {
            if let Some(TypedHeader(headers::Authorization(bearer))) = auth_header {
                if let Ok(()) =
                    auth.verify_server_token(bearer.token(), current_time_epoch_millis())
                {
                    return Ok(());
                }
                return Err(AppError::auth(
                    StatusCode::UNAUTHORIZED,
                    anyhow!("Unauthorized."),
                    "invalid_server_token",
                ));
            }
            Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("Unauthorized."),
                "missing_token",
            ))
        } else {
            Ok(())
        }
    }

    pub async fn redact_error_middleware(req: Request, next: Next) -> impl IntoResponse {
        let resp = next.run(req).await;
        if resp.status().is_server_error() || resp.status().is_client_error() {
            // If we should redact errors, copy over only the status code and
            // not the response body.
            return resp.status().into_response();
        }
        resp
    }

    pub async fn version_header_middleware(req: Request, next: Next) -> impl IntoResponse {
        let mut resp = next.run(req).await;
        resp.headers_mut().insert(
            HeaderName::from_static("relay-server-version"),
            HeaderValue::from_static(RELAY_SERVER_VERSION),
        );
        resp
    }

    pub fn routes_with_metrics(self: &Arc<Self>) -> Router {
        self.routes().layer(middleware::from_fn_with_state(
            self.clone(),
            auth_metrics_middleware,
        ))
    }

    pub fn routes(self: &Arc<Self>) -> Router {
        let mut router = Router::new()
            .route("/ready", get(ready))
            .route("/check_store", post(check_store))
            .route("/check_store", get(check_store_deprecated))
            .route("/doc/ws/:doc_id", get(handle_socket_upgrade_deprecated))
            .route("/doc/new", post(new_doc))
            .route("/doc/:doc_id/auth", post(auth_doc))
            .route("/doc/:doc_id/as-update", get(get_doc_as_update_deprecated))
            .route("/doc/:doc_id/update", post(update_doc_deprecated))
            .route("/d/:doc_id/as-update", get(get_doc_as_update))
            .route("/d/:doc_id/update", post(update_doc))
            .route("/d/:doc_id/versions", get(handle_doc_versions))
            .route(
                "/d/:doc_id/ws/:doc_id2",
                get(handle_socket_upgrade_full_path),
            )
            .route("/webhook/reload", post(reload_webhook_config_endpoint));

        // Only add file endpoints if a store is configured
        if let Some(store) = &self.store {
            // Add presigned URL endpoints for all stores
            router = router
                .route("/f/:doc_id/upload-url", post(handle_file_upload_url))
                .route("/f/:doc_id/download-url", get(handle_file_download_url));

            // Add file operations that work with any store
            router = router
                .route("/f/:doc_id/history", get(handle_file_history))
                .route("/f/:doc_id", delete(handle_file_delete))
                .route("/f/:doc_id/:hash", delete(handle_file_delete_by_hash))
                .route("/f/:doc_id", head(handle_file_head));

            // Only add direct upload/download endpoints if store supports direct uploads
            if store.supports_direct_uploads() {
                let upload_routes = Router::new()
                    .route(
                        "/f/:doc_id/upload",
                        post(handle_file_upload).put(handle_file_upload_raw),
                    )
                    .route("/f/:doc_id/download", get(handle_file_download))
                    .layer(DefaultBodyLimit::max(250 * 1024 * 1024));
                router = router.merge(upload_routes);
            }
        }

        router.with_state(self.clone())
    }

    pub fn metrics_routes(self: &Arc<Self>) -> Router {
        Router::new()
            .route("/metrics", get(metrics_endpoint))
            .with_state(self.clone())
    }

    async fn serve_internal(
        self: Arc<Self>,
        listener: TcpListener,
        redact_errors: bool,
        routes: Router,
    ) -> Result<()> {
        let token = self.cancellation_token.clone();

        let app = routes.layer(middleware::from_fn(Self::version_header_middleware));
        let app = if redact_errors {
            app
        } else {
            app.layer(middleware::from_fn(Self::redact_error_middleware))
        };

        tracing::info!("Starting HTTP server...");
        axum::serve(listener, app.into_make_service())
            .with_graceful_shutdown(async move {
                tracing::info!("Waiting for cancellation token...");
                token.cancelled().await;
                tracing::info!("Cancellation token triggered, starting graceful shutdown");
            })
            .await?;

        tracing::info!("HTTP server stopped, shutting down event dispatcher...");

        // Explicitly shutdown event dispatcher before waiting on doc workers
        if let Some(event_dispatcher) = &self.event_dispatcher {
            tracing::info!("Shutting down event dispatcher...");
            event_dispatcher.shutdown();
            tracing::info!("Event dispatcher shutdown complete");
        }

        tracing::info!("Closing doc worker tracker...");
        self.doc_worker_tracker.close();
        tracing::info!("Waiting for doc workers to finish...");
        self.doc_worker_tracker.wait().await;
        tracing::info!("All doc workers stopped");

        Ok(())
    }

    pub async fn serve(self, listener: TcpListener, redact_errors: bool) -> Result<()> {
        let s = Arc::new(self);
        let routes = s.routes_with_metrics();
        s.serve_internal(listener, redact_errors, routes).await
    }

    pub async fn serve_metrics(self, listener: TcpListener) -> Result<()> {
        let s = Arc::new(self);
        let routes = s.metrics_routes();
        s.serve_internal(listener, false, routes).await
    }

    async fn ensure_socket_doc_access(
        &self,
        doc_id: &str,
        authorization: Authorization,
    ) -> Result<(), AppError> {
        if !matches!(authorization, Authorization::Full) && !self.doc_exists(doc_id).await {
            return Err(AppError::new(
                StatusCode::NOT_FOUND,
                anyhow!("Doc {} not found", doc_id),
            ));
        }

        Ok(())
    }

    fn verify_doc_token(&self, token: Option<&str>, doc: &str) -> Result<Authorization, AppError> {
        if let Some(authenticator) = &self.authenticator {
            if let Some(token) = token {
                let authorization = authenticator
                    .verify_doc_token(token, doc, current_time_epoch_millis())
                    .map_err(|e| {
                        AppError::auth(StatusCode::UNAUTHORIZED, e.into(), "invalid_doc_token")
                    })?;
                Ok(authorization)
            } else {
                Err(AppError::auth(
                    StatusCode::UNAUTHORIZED,
                    anyhow!("No token provided."),
                    "missing_token",
                ))
            }
        } else {
            Ok(Authorization::Full)
        }
    }
}

#[derive(Deserialize)]
struct HandlerParams {
    token: Option<String>,
}

async fn get_doc_as_update(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<Response, AppError> {
    // All authorization types allow reading the document.
    let token = get_token_from_header(auth_header);
    let _ = server_state.verify_doc_token(token.as_deref(), &doc_id)?;

    let dwskv = server_state
        .get_or_create_doc(&doc_id)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;

    let update = dwskv.as_update();
    tracing::debug!("update: {:?}", update);
    Ok(update.into_response())
}

async fn get_doc_as_update_deprecated(
    Path(doc_id): Path<String>,
    State(server_state): State<Arc<Server>>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<Response, AppError> {
    tracing::warn!("/doc/:doc_id/as-update is deprecated; call /doc/:doc_id/auth instead and then call as-update on the returned base URL.");
    get_doc_as_update(State(server_state), Path(doc_id), auth_header).await
}

async fn update_doc_deprecated(
    Path(doc_id): Path<String>,
    State(server_state): State<Arc<Server>>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
    body: Bytes,
) -> Result<Response, AppError> {
    tracing::warn!("/doc/:doc_id/update is deprecated; call /doc/:doc_id/auth instead and then call update on the returned base URL.");
    update_doc(Path(doc_id), State(server_state), auth_header, body).await
}

async fn update_doc(
    Path(doc_id): Path<String>,
    State(server_state): State<Arc<Server>>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
    body: Bytes,
) -> Result<Response, AppError> {
    let token = get_token_from_header(auth_header);
    let authorization = server_state.verify_doc_token(token.as_deref(), &doc_id)?;
    update_doc_inner(doc_id, server_state, authorization, body).await
}

async fn update_doc_inner(
    doc_id: String,
    server_state: Arc<Server>,
    authorization: Authorization,
    body: Bytes,
) -> Result<Response, AppError> {
    if !matches!(authorization, Authorization::Full) {
        return Err(AppError::auth(
            StatusCode::FORBIDDEN,
            anyhow!("Unauthorized."),
            "insufficient_permissions",
        ));
    }

    let dwskv = server_state
        .get_or_create_doc(&doc_id)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;

    if let Err(err) = dwskv.apply_update(&body) {
        tracing::error!(?err, "Failed to apply update");
        return Err(AppError::new(StatusCode::INTERNAL_SERVER_ERROR, err));
    }

    Ok(StatusCode::OK.into_response())
}

async fn handle_socket_upgrade_with_channel_and_user(
    ws: WebSocketUpgrade,
    Path(doc_id): Path<String>,
    authorization: Authorization,
    routing_channel: Option<String>,
    user: Option<String>,
    token: Option<String>,
    State(server_state): State<Arc<Server>>,
) -> Result<Response, AppError> {
    server_state
        .ensure_socket_doc_access(&doc_id, authorization)
        .await?;

    // Extract expiration time from token
    let expiration_time = if let Some(authenticator) = &server_state.authenticator {
        if let Some(token_str) = token.as_deref() {
            authenticator
                .decode_token(token_str)
                .ok()
                .and_then(|payload| payload.expiration_millis)
                .map(|exp| exp.0)
        } else {
            None
        }
    } else {
        None
    };

    let user_for_pud = user.clone();
    let dwskv = server_state
        .get_or_create_doc_with_channel_and_user(&doc_id, routing_channel, user)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;
    let awareness = dwskv.awareness();
    let sync_kv = dwskv.sync_kv();
    let cancellation_token = server_state.cancellation_token.clone();
    let sync_protocol_event_sender = server_state.sync_protocol_event_sender.clone();
    let metrics = server_state.metrics.clone();
    let doc_id_clone = doc_id.clone();

    Ok(ws.on_upgrade(move |socket| {
        handle_socket(
            socket,
            awareness,
            sync_kv,
            authorization,
            expiration_time,
            user_for_pud,
            cancellation_token,
            sync_protocol_event_sender,
            doc_id_clone,
            metrics,
        )
    }))
}

fn verify_socket_token(
    server_state: &Arc<Server>,
    doc_id: &str,
    token: Option<&str>,
) -> Result<(Authorization, Option<String>, Option<String>), AppError> {
    let (permission, channel) = if let Some(authenticator) = &server_state.authenticator {
        let token = token.ok_or_else(|| {
            AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided."),
                "missing_token",
            )
        })?;

        authenticator
            .verify_token_with_channel(token, current_time_epoch_millis())
            .map_err(|e| {
                tracing::debug!("Token verification failed: {:?}", e);
                // Distinguish expired/invalid_signature/key_mismatch/etc instead of
                // collapsing every WS-upgrade auth failure into "invalid_token" —
                // matches validate_file_token's use of the same AuthError::to_metric_label(),
                // and was the missing signal in #75491f2f's recurrence diagnosis (2026-09-17):
                // the sole error series on /d/:doc_id/ws/:doc_id2 gave no way to tell a token
                // clock-skew/expiry problem apart from a signature/key mismatch.
                let error_type = e.to_metric_label();
                if matches!(e, y_sweet_core::auth::AuthError::Expired) {
                    // Diagnostic for #cde2a0b7: HOW expired — race at the TTL boundary
                    // (~1-2s), stale cached token (minutes+) or clock skew (constant N s).
                    // Value only; the token and its claims are never logged or labelled.
                    if let Some(overshoot) =
                        authenticator.expired_overshoot_secs(token, current_time_epoch_millis())
                    {
                        server_state
                            .metrics
                            .record_ws_token_expired_overshoot(overshoot);
                    }
                }
                AppError::auth(StatusCode::UNAUTHORIZED, e.into(), error_type)
            })?
    } else {
        (Permission::Server, None)
    };

    let (authorization, user) = match permission {
        Permission::Doc(doc_perm) => {
            if doc_perm.doc_id != doc_id {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Token not valid for this document"),
                    "access_wrong_document",
                ));
            }
            (doc_perm.authorization, doc_perm.user)
        }
        Permission::Server => (Authorization::Full, None),
        Permission::Prefix(prefix_perm) => {
            if !doc_id.starts_with(&prefix_perm.prefix) {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Token not valid for this document"),
                    "prefix_mismatch",
                ));
            }
            (prefix_perm.authorization, prefix_perm.user)
        }
        Permission::File(_) => {
            return Err(AppError::auth(
                StatusCode::FORBIDDEN,
                anyhow!("File token not valid for document access"),
                "wrong_token_type",
            ));
        }
    };

    Ok((authorization, channel, user))
}

async fn handle_socket_upgrade_deprecated(
    ws: WebSocketUpgrade,
    Path(doc_id): Path<String>,
    Query(params): Query<HandlerParams>,
    State(server_state): State<Arc<Server>>,
) -> Result<Response, AppError> {
    tracing::warn!(
        "/doc/ws/:doc_id is deprecated; call /doc/:doc_id/auth instead and use the returned URL."
    );
    let (authorization, channel, user) =
        verify_socket_token(&server_state, &doc_id, params.token.as_deref())?;

    handle_socket_upgrade_with_channel_and_user(
        ws,
        Path(doc_id),
        authorization,
        channel,
        user,
        params.token.clone(), // Pass the token from query params
        State(server_state),
    )
    .await
}

async fn handle_socket_upgrade_full_path(
    ws: WebSocketUpgrade,
    Path((doc_id, doc_id2)): Path<(String, String)>,
    Query(params): Query<HandlerParams>,
    State(server_state): State<Arc<Server>>,
) -> Result<Response, AppError> {
    tracing::debug!("WebSocket upgrade request for doc: {}", doc_id);

    if doc_id != doc_id2 {
        tracing::debug!("Doc ID mismatch: {} != {}", doc_id, doc_id2);
        return Err(AppError::new(
            StatusCode::BAD_REQUEST,
            anyhow!("For Yjs compatibility, the doc_id appears twice in the URL. It must be the same in both places, but we got {} and {}.", doc_id, doc_id2),
        ));
    }

    let (authorization, channel, user) =
        verify_socket_token(&server_state, &doc_id, params.token.as_deref())?;

    handle_socket_upgrade_with_channel_and_user(
        ws,
        Path(doc_id),
        authorization,
        channel,
        user,
        params.token.clone(), // Pass the token from query params
        State(server_state),
    )
    .await
}

async fn handle_socket(
    socket: WebSocket,
    awareness: Arc<RwLock<Awareness>>,
    sync_kv: Arc<SyncKv>,
    authorization: Authorization,
    expiration_time: Option<u64>,
    user: Option<String>,
    cancellation_token: CancellationToken,
    sync_protocol_event_sender: Arc<SyncProtocolEventSender>,
    doc_id: String,
    metrics: Arc<RelayMetrics>,
) {
    let (mut sink, mut stream) = socket.split();
    let (send, mut recv) = channel(1024);

    tokio::spawn(async move {
        while let Some(msg) = recv.recv().await {
            let _ = sink.send(msg).await;
        }
    });

    let send_clone = send.clone();
    let mut conn = DocConnection::new_with_expiration(
        awareness,
        authorization,
        expiration_time,
        move |bytes| {
            if let Err(e) = send_clone.try_send(Message::Binary(bytes.to_vec())) {
                tracing::warn!(?e, "Error sending message");
            }
        },
    );
    conn.set_sync_kv(sync_kv);
    if let Some(user) = user {
        conn.set_user(user);
    }
    let connection = Arc::new(conn);

    // Register the connection with the sync protocol event sender
    sync_protocol_event_sender.register_doc_connection(doc_id.clone(), Arc::downgrade(&connection));

    loop {
        tokio::select! {
            Some(msg) = stream.next() => {
                let msg = match msg {
                    Ok(Message::Binary(bytes)) => bytes,
                    Ok(Message::Close(_)) => break,
                    Err(_e) => {
                        // The stream will complain about things like
                        // connections being lost without handshake.
                        continue;
                    }
                    msg => {
                        tracing::warn!(?msg, "Received non-binary message");
                        continue;
                    }
                };

                match connection.send(&msg).await {
                    Ok(_) => {},
                    Err(e) if e.to_string().contains("Token expired") => {
                        metrics.record_http_auth_error(
                            "expired",
                            "1008",
                            "websocket_connection",
                            "WS",
                        );
                        tracing::warn!(
                            doc_id = %doc_id,
                            "Closing connection due to token expiration"
                        );
                        let _ = send.try_send(Message::Close(Some(CloseFrame {
                            code: 1008, // Policy Violation - indicates a policy violation
                            reason: "Token expired".into(),
                        })));
                        break;
                    }
                    Err(e) => {
                        tracing::warn!(?e, "Error handling message");
                    }
                }
            }
            _ = cancellation_token.cancelled() => {
                tracing::debug!("Closing doc connection due to server cancel...");
                break;
            }
        }
    }
}

async fn check_store(
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
    State(server_state): State<Arc<Server>>,
) -> Result<Json<Value>, AppError> {
    server_state.check_auth(auth_header)?;

    if server_state.store.is_none() {
        return Ok(Json(json!({"ok": false, "error": "No store set."})));
    };

    // The check_store endpoint for the native server is kind of moot, since
    // the server will not start if store is not ok.
    Ok(Json(json!({"ok": true})))
}

async fn check_store_deprecated(
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
    State(server_state): State<Arc<Server>>,
) -> Result<Json<Value>, AppError> {
    tracing::warn!(
        "GET check_store is deprecated, use POST check_store with an empty body instead."
    );
    check_store(auth_header, State(server_state)).await
}

/// Always returns a 200 OK response, as long as we are listening.
async fn ready() -> Result<Json<Value>, AppError> {
    Ok(Json(json!({"ok": true})))
}

async fn new_doc(
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
    State(server_state): State<Arc<Server>>,
    Json(body): Json<DocCreationRequest>,
) -> Result<Json<NewDocResponse>, AppError> {
    let token = get_token_from_header(auth_header);

    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // First try server token
            if authenticator
                .verify_server_token(token, current_time_epoch_millis())
                .is_ok()
            {
                // Server token allows creating any document
            } else {
                // Try prefix token - we need to check if the doc_id matches the prefix
                if let Some(doc_id) = &body.doc_id {
                    let permission = authenticator
                        .verify_token_auto(token, current_time_epoch_millis())
                        .map_err(|auth_error| {
                            AppError::auth(
                                StatusCode::UNAUTHORIZED,
                                anyhow!("Invalid token: {}", auth_error),
                                auth_error.to_metric_label(),
                            )
                        })?;

                    match permission {
                        Permission::Prefix(prefix_perm) => {
                            // Check if the document ID starts with the prefix
                            if !doc_id.starts_with(&prefix_perm.prefix) {
                                return Err(AppError::auth(
                                    StatusCode::FORBIDDEN,
                                    anyhow!(
                                        "Document ID '{}' does not match prefix '{}'",
                                        doc_id,
                                        prefix_perm.prefix
                                    ),
                                    "prefix_mismatch",
                                ));
                            }
                            // Check if we have Full permissions (needed for creation)
                            if prefix_perm.authorization != Authorization::Full {
                                return Err(AppError::auth(
                                    StatusCode::FORBIDDEN,
                                    anyhow!("Prefix token requires Full authorization to create documents"),
                                    "insufficient_permissions",
                                ));
                            }
                        }
                        _ => {
                            return Err(AppError::auth(
                                StatusCode::FORBIDDEN,
                                anyhow!("Only server or prefix tokens can create documents"),
                                "wrong_token_type",
                            ));
                        }
                    }
                } else {
                    // No doc_id provided - only server tokens can create with auto-generated ID
                    return Err(AppError::auth(
                        StatusCode::FORBIDDEN,
                        anyhow!("Prefix tokens must specify a docId that matches their prefix"),
                        "wrong_token_type",
                    ));
                }
            }
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    }

    let doc_id = if let Some(doc_id) = body.doc_id {
        if !validate_doc_name(doc_id.as_str()) {
            Err((StatusCode::BAD_REQUEST, anyhow!("Invalid document name")))?
        }

        server_state
            .get_or_create_doc(doc_id.as_str())
            .await
            .map_err(|e| {
                tracing::error!(?e, "Failed to create doc");
                (StatusCode::INTERNAL_SERVER_ERROR, e)
            })?;

        doc_id
    } else {
        server_state.create_doc().await.map_err(|d| {
            tracing::error!(?d, "Failed to create doc");
            (StatusCode::INTERNAL_SERVER_ERROR, d)
        })?
    };

    Ok(Json(NewDocResponse { doc_id }))
}

fn generate_base_url(
    url: &Option<Url>,
    allowed_hosts: &[AllowedHost],
    request_host: &str,
) -> Result<String, AppError> {
    // Priority 1: Explicit URL prefix
    if let Some(prefix) = url {
        return Ok(prefix.as_str().trim_end_matches('/').to_string());
    }

    // Priority 2: Context-derived URL from Host header
    if let Some(allowed) = allowed_hosts.iter().find(|h| h.host == request_host) {
        return Ok(format!("{}://{}", allowed.scheme, request_host));
    }

    // Priority 3: Fallback to old behavior for backward compatibility
    if allowed_hosts.is_empty() {
        return Ok(format!("http://{}", request_host));
    }

    // Reject unknown hosts when allowed_hosts is configured
    Err(AppError::new(
        StatusCode::BAD_REQUEST,
        anyhow!("Host '{}' not in allowed hosts list", request_host),
    ))
}

fn generate_context_aware_urls(
    url: &Option<Url>,
    allowed_hosts: &[AllowedHost],
    request_host: &str,
    doc_id: &str,
) -> Result<(String, String), AppError> {
    // Priority 1: Explicit URL prefix
    if let Some(prefix) = url {
        let ws_scheme = if prefix.scheme() == "https" {
            "wss"
        } else {
            "ws"
        };
        let mut ws_url = prefix.clone();
        ws_url.set_scheme(ws_scheme).unwrap();
        let ws_url = ws_url
            .join(&format!("/d/{}/ws", doc_id))
            .unwrap()
            .to_string();

        let base_url = format!("{}/d/{}", prefix.as_str().trim_end_matches('/'), doc_id);
        return Ok((ws_url, base_url));
    }

    // Priority 2: Context-derived URL from Host header
    if let Some(allowed) = allowed_hosts.iter().find(|h| h.host == request_host) {
        let ws_scheme = if allowed.scheme == "https" {
            "wss"
        } else {
            "ws"
        };
        let ws_url = format!("{}://{}/d/{}/ws", ws_scheme, request_host, doc_id);
        let base_url = format!("{}://{}/d/{}", allowed.scheme, request_host, doc_id);
        return Ok((ws_url, base_url));
    }

    // Priority 3: Fallback to old behavior for backward compatibility
    // This handles the case where no URL prefix and no allowed hosts are set
    if allowed_hosts.is_empty() {
        let ws_url = format!("ws://{}/d/{}/ws", request_host, doc_id);
        let base_url = format!("http://{}/d/{}", request_host, doc_id);
        return Ok((ws_url, base_url));
    }

    // Reject unknown hosts when allowed_hosts is configured
    Err(AppError::new(
        StatusCode::BAD_REQUEST,
        anyhow!("Host '{}' not in allowed hosts list", request_host),
    ))
}

async fn auth_doc(
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
    TypedHeader(host): TypedHeader<headers::Host>,
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    body: Option<Json<AuthDocRequest>>,
) -> Result<Json<ClientToken>, AppError> {
    server_state.check_auth(auth_header)?;

    let Json(AuthDocRequest {
        authorization,
        valid_for_seconds,
        ..
    }) = body.unwrap_or_default();

    if !server_state.doc_exists(&doc_id).await {
        Err((StatusCode::NOT_FOUND, anyhow!("Doc {} not found", doc_id)))?;
    }

    let valid_for_seconds = valid_for_seconds.unwrap_or(DEFAULT_EXPIRATION_SECONDS);
    let expiration_time =
        ExpirationTimeEpochMillis(current_time_epoch_millis() + valid_for_seconds * 1000);

    let token = if let Some(auth) = &server_state.authenticator {
        let token = auth
            .gen_doc_token(&doc_id, authorization, expiration_time, None)
            .map_err(|e| {
                AppError::new(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    anyhow!("Failed to generate token: {}", e),
                )
            })?;
        Some(token)
    } else {
        None
    };

    let (url, base_url) = generate_context_aware_urls(
        &server_state.url,
        &server_state.allowed_hosts,
        &host.to_string(),
        &doc_id,
    )?;

    Ok(Json(ClientToken {
        url,
        base_url: Some(base_url),
        doc_id,
        token,
        authorization,
    }))
}

fn get_token_from_header(
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Option<String> {
    if let Some(TypedHeader(headers::Authorization(bearer))) = auth_header {
        Some(bearer.token().to_string())
    } else {
        None
    }
}

async fn handle_file_upload_url(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    TypedHeader(host): TypedHeader<headers::Host>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<Json<FileUploadUrlResponse>, AppError> {
    tracing::info!(doc_id = %doc_id, "Generating file upload URL");

    // Get token and extract metadata
    let token = get_token_from_header(auth_header);

    // Verify that the token is for the requested document and extract file hash from token
    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // Verify token is for this doc_id
            let auth = authenticator
                .verify_file_token_for_doc(token, &doc_id, current_time_epoch_millis())
                .map_err(|e| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token: {}", e),
                        "invalid_token",
                    )
                })?;

            // Only allow Full permission to upload
            if !matches!(auth, Authorization::Full) {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Insufficient permissions to upload files"),
                    "insufficient_permissions",
                ));
            }

            // Verify the token and get the file metadata
            let permission = authenticator
                .verify_token_auto(token, current_time_epoch_millis())
                .map_err(|_| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token"),
                        "invalid_token",
                    )
                })?;

            if let Permission::File(file_permission) = permission {
                let file_hash = file_permission.file_hash;

                // Validate the file hash
                if !validate_file_hash(&file_hash) {
                    return Err(AppError::new(
                        StatusCode::BAD_REQUEST,
                        anyhow!("Invalid file hash format in token"),
                    ));
                }

                // Check if we have a store configured
                if server_state.store.is_none() {
                    return Err(AppError::new(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        anyhow!("No store configured for file uploads"),
                    ));
                }

                // Get metadata from token
                let content_type = file_permission.content_type.as_deref();
                let content_length = file_permission.content_length;

                // Generate the upload URL - organize files by doc_id/file_hash
                let key = format!("files/{}/{}", doc_id, file_hash);
                let upload_url = server_state
                    .store
                    .as_ref()
                    .unwrap()
                    .generate_upload_url(&key, content_type, content_length)
                    .await
                    .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

                if let Some(url) = upload_url {
                    // Check if this is a local endpoint (relative path) and convert to full URL with token
                    if !url.starts_with("http") {
                        let base_url = generate_base_url(
                            &server_state.url,
                            &server_state.allowed_hosts,
                            &host.to_string(),
                        )?;
                        let full_url = format!("{}{}?token={}", base_url, url, token);
                        return Ok(Json(FileUploadUrlResponse {
                            upload_url: full_url,
                        }));
                    } else {
                        // S3/cloud storage URL - return as-is
                        return Ok(Json(FileUploadUrlResponse { upload_url: url }));
                    }
                } else {
                    return Err(AppError::new(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        anyhow!("Failed to generate upload URL"),
                    ));
                }
            } else {
                return Err(AppError::new(
                    StatusCode::BAD_REQUEST,
                    anyhow!("Token is not a file token"),
                ));
            }
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    } else {
        // No auth configured, anyone can upload
        return Err(AppError::auth(
            StatusCode::UNAUTHORIZED,
            anyhow!("Authentication is required for file operations"),
            "no_authenticator",
        ));
    }
}

async fn handle_file_download_url(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    TypedHeader(host): TypedHeader<headers::Host>,
    Query(params): Query<FileDownloadQueryParams>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<Json<FileDownloadUrlResponse>, AppError> {
    tracing::info!(doc_id = %doc_id, hash = ?params.hash, "Generating file download URL");

    // Get token
    let token = get_token_from_header(auth_header);

    // Check if we have authentication configured
    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // Extract hash from query parameter if present
            let query_hash = params.hash;

            // Verify the token and determine its type
            let permission = authenticator
                .verify_token_auto(token, current_time_epoch_millis())
                .map_err(|_| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token"),
                        "invalid_token",
                    )
                })?;

            match permission {
                Permission::File(file_permission) => {
                    // Check if file token is for this doc_id
                    if file_permission.doc_id != doc_id {
                        return Err(AppError::auth(
                            StatusCode::UNAUTHORIZED,
                            anyhow!("Token not valid for this document"),
                            "access_wrong_document",
                        ));
                    }

                    // Both ReadOnly and Full can download files
                    if !matches!(
                        file_permission.authorization,
                        Authorization::ReadOnly | Authorization::Full
                    ) {
                        return Err(AppError::auth(
                            StatusCode::FORBIDDEN,
                            anyhow!("Insufficient permissions to download file"),
                            "insufficient_permissions",
                        ));
                    }

                    let file_hash = file_permission.file_hash;

                    // Validate the file hash
                    if !validate_file_hash(&file_hash) {
                        return Err(AppError::new(
                            StatusCode::BAD_REQUEST,
                            anyhow!("Invalid file hash format in token"),
                        ));
                    }

                    // Generate download URL using hash from token
                    let Json(download_response) = generate_file_download_url(
                        &server_state,
                        &doc_id,
                        &file_hash,
                        &host.to_string(),
                    )
                    .await?;
                    // Add token to the URL
                    let mut download_url = download_response.download_url;
                    if !download_url.starts_with("http") || download_url.contains("/f/") {
                        // This is our local endpoint, add token
                        let separator = if download_url.contains('?') { "&" } else { "?" };
                        download_url = format!("{}{}token={}", download_url, separator, token);
                    }
                    return Ok(Json(FileDownloadUrlResponse { download_url }));
                }
                Permission::Server => {
                    // Server token is valid, use hash from query parameter
                    if let Some(hash) = query_hash {
                        // Validate the file hash from query parameter
                        if !validate_file_hash(&hash) {
                            return Err(AppError::new(
                                StatusCode::BAD_REQUEST,
                                anyhow!("Invalid file hash format in query parameter"),
                            ));
                        }

                        // Generate download URL using hash from query parameter
                        let Json(download_response) = generate_file_download_url(
                            &server_state,
                            &doc_id,
                            &hash,
                            &host.to_string(),
                        )
                        .await?;
                        // Add token to the URL
                        let mut download_url = download_response.download_url;
                        if !download_url.starts_with("http") || download_url.contains("/f/") {
                            // This is our local endpoint, add token
                            let separator = if download_url.contains('?') { "&" } else { "?" };
                            download_url = format!("{}{}token={}", download_url, separator, token);
                        }
                        return Ok(Json(FileDownloadUrlResponse { download_url }));
                    } else {
                        return Err(AppError::new(
                            StatusCode::BAD_REQUEST,
                            anyhow!("Hash query parameter required when using server token"),
                        ));
                    }
                }
                Permission::Doc(_) => {
                    return Err(AppError::new(
                        StatusCode::BAD_REQUEST,
                        anyhow!("Document tokens cannot be used for file operations"),
                    ));
                }
                Permission::Prefix(prefix_perm) => {
                    // Check if doc_id matches the prefix
                    if !doc_id.starts_with(&prefix_perm.prefix) {
                        return Err(AppError::auth(
                            StatusCode::FORBIDDEN,
                            anyhow!("Token not valid for this document"),
                            "prefix_mismatch",
                        ));
                    }

                    // Both ReadOnly and Full can download files
                    if !matches!(
                        prefix_perm.authorization,
                        Authorization::ReadOnly | Authorization::Full
                    ) {
                        return Err(AppError::auth(
                            StatusCode::FORBIDDEN,
                            anyhow!("Insufficient permissions to download file"),
                            "insufficient_permissions",
                        ));
                    }

                    // Use hash from query parameter for prefix tokens
                    if let Some(hash) = query_hash {
                        // Validate the file hash from query parameter
                        if !validate_file_hash(&hash) {
                            return Err(AppError::new(
                                StatusCode::BAD_REQUEST,
                                anyhow!("Invalid file hash format in query parameter"),
                            ));
                        }

                        // Generate download URL using hash from query parameter
                        let Json(download_response) = generate_file_download_url(
                            &server_state,
                            &doc_id,
                            &hash,
                            &host.to_string(),
                        )
                        .await?;
                        // Add token to the URL
                        let mut download_url = download_response.download_url;
                        if !download_url.starts_with("http") || download_url.contains("/f/") {
                            // This is our local endpoint, add token
                            let separator = if download_url.contains('?') { "&" } else { "?" };
                            download_url = format!("{}{}token={}", download_url, separator, token);
                        }
                        return Ok(Json(FileDownloadUrlResponse { download_url }));
                    } else {
                        return Err(AppError::new(
                            StatusCode::BAD_REQUEST,
                            anyhow!("Hash query parameter required when using prefix token"),
                        ));
                    }
                }
            }
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    } else {
        // No auth configured
        return Err(AppError::auth(
            StatusCode::UNAUTHORIZED,
            anyhow!("Authentication is required for file operations"),
            "no_authenticator",
        ));
    }
}

async fn generate_file_download_url(
    server_state: &Arc<Server>,
    doc_id: &str,
    file_hash: &str,
    host: &str,
) -> Result<Json<FileDownloadUrlResponse>, AppError> {
    // Check if we have a store configured
    if server_state.store.is_none() {
        return Err(AppError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            anyhow!("No store configured for file downloads"),
        ));
    }

    // Generate the download URL - using doc_id/file_hash path structure
    let key = format!("files/{}/{}", doc_id, file_hash);
    let download_url = server_state
        .store
        .as_ref()
        .unwrap()
        .generate_download_url(&key)
        .await
        .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

    if let Some(url) = download_url {
        // Check if this is a local endpoint (relative path) and convert to full URL
        if !url.starts_with("http") {
            let base_url = generate_base_url(&server_state.url, &server_state.allowed_hosts, host)?;
            let full_url = format!("{}{}", base_url, url);
            Ok(Json(FileDownloadUrlResponse {
                download_url: full_url,
            }))
        } else {
            // S3/cloud storage URL - return as-is
            Ok(Json(FileDownloadUrlResponse { download_url: url }))
        }
    } else {
        Err(AppError::new(
            StatusCode::NOT_FOUND,
            anyhow!("File not found"),
        ))
    }
}

/// Delete all files for a document
///
/// This endpoint accepts either:
/// - A file token with the doc_id (hash not required)
/// - A doc token with the doc_id
/// - A server token
///
/// Returns 204 No Content on success
async fn handle_file_delete(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<StatusCode, AppError> {
    // Get token
    let token = get_token_from_header(auth_header);

    // Verify token is for this doc_id and has required permission
    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // Verify token is for this doc_id
            let auth = authenticator
                .verify_file_token_for_doc(token, &doc_id, current_time_epoch_millis())
                .map_err(|e| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token: {}", e),
                        "invalid_token",
                    )
                })?;

            // Only Full permission can delete files
            if !matches!(auth, Authorization::Full) {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Insufficient permissions to delete files"),
                    "insufficient_permissions",
                ));
            }

            // Check if we have a store configured
            if server_state.store.is_none() {
                return Err(AppError::new(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    anyhow!("No store configured for file operations"),
                ));
            }

            // List all files in the document's directory
            let prefix = format!("files/{}/", doc_id);
            let store = server_state.store.as_ref().unwrap();

            let file_infos = store
                .list(&prefix)
                .await
                .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

            if file_infos.is_empty() {
                tracing::info!("No files to delete for document: {}", doc_id);
                return Ok(StatusCode::NO_CONTENT);
            }

            // Delete each file
            let mut deleted_count = 0;
            for file_info in file_infos {
                if let Err(e) = store.remove(&file_info.key).await {
                    tracing::error!("Failed to delete file {}: {}", file_info.key, e);
                    continue;
                }
                deleted_count += 1;
            }

            tracing::info!("Deleted {} files for document: {}", deleted_count, doc_id);
            return Ok(StatusCode::NO_CONTENT);
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    } else {
        // No auth configured
        return Err(AppError::auth(
            StatusCode::UNAUTHORIZED,
            anyhow!("Authentication is required for file operations"),
            "no_authenticator",
        ));
    }
}

/// Delete a specific file by hash
///
/// This endpoint accepts either:
/// - A file token with the doc_id (hash not required)
/// - A doc token with the doc_id
/// - A server token
///
/// The hash to delete is specified in the URL path.
/// Returns 204 No Content on success, 404 if file not found
async fn handle_file_delete_by_hash(
    State(server_state): State<Arc<Server>>,
    Path((doc_id, file_hash)): Path<(String, String)>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<StatusCode, AppError> {
    // Get token
    let token = get_token_from_header(auth_header);

    // Verify token is for this doc_id and has required permission
    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // Verify token is for this doc_id
            let auth = authenticator
                .verify_file_token_for_doc(token, &doc_id, current_time_epoch_millis())
                .map_err(|e| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token: {}", e),
                        "invalid_token",
                    )
                })?;

            // Only Full permission can delete files
            if !matches!(auth, Authorization::Full) {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Insufficient permissions to delete file"),
                    "insufficient_permissions",
                ));
            }

            // Validate the file hash format
            if !validate_file_hash(&file_hash) {
                return Err(AppError::new(
                    StatusCode::BAD_REQUEST,
                    anyhow!("Invalid file hash format"),
                ));
            }

            // Check if we have a store configured
            if server_state.store.is_none() {
                return Err(AppError::new(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    anyhow!("No store configured for file operations"),
                ));
            }

            // Construct the file path
            let key = format!("files/{}/{}", doc_id, file_hash);

            // Check if the file exists before trying to delete it
            let exists = server_state
                .store
                .as_ref()
                .unwrap()
                .exists(&key)
                .await
                .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

            if !exists {
                // If the file is already gone, return 204 No Content since DELETE is idempotent
                tracing::debug!("File already deleted: {}/{}", doc_id, file_hash);
                return Ok(StatusCode::NO_CONTENT);
            }

            // Delete the file
            server_state
                .store
                .as_ref()
                .unwrap()
                .remove(&key)
                .await
                .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

            tracing::info!("Deleted file: {}/{}", doc_id, file_hash);
            return Ok(StatusCode::NO_CONTENT);
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    } else {
        // No auth configured
        return Err(AppError::auth(
            StatusCode::UNAUTHORIZED,
            anyhow!("Authentication is required for file operations"),
            "no_authenticator",
        ));
    }
}

/// Handle HEAD request to check if a file exists in S3 storage
///
/// Returns:
/// - 200 OK if the file exists
/// - 404 Not Found if the file doesn't exist
/// - Other status codes for authentication/authorization errors

/// Get the history of all files for a document
///
/// This endpoint accepts either:
/// - A file token with the doc_id (hash not required)
/// - A doc token with the doc_id
/// - A server token
async fn handle_file_history(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<Json<FileHistoryResponse>, AppError> {
    // Get token
    let token = get_token_from_header(auth_header);

    // Verify token is for this doc_id
    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // Verify token is for this doc_id - this now accepts both doc and file tokens
            let auth = authenticator
                .verify_file_token_for_doc(token, &doc_id, current_time_epoch_millis())
                .map_err(|e| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token: {}", e),
                        "invalid_token",
                    )
                })?;

            // Both ReadOnly and Full can view file history
            if !matches!(auth, Authorization::ReadOnly | Authorization::Full) {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Insufficient permissions to view file history"),
                    "insufficient_permissions",
                ));
            }
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    }

    // Check if we have a store configured
    if server_state.store.is_none() {
        return Err(AppError::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            anyhow!("No store configured for file operations"),
        ));
    }

    // List files in the document's directory
    let prefix = format!("files/{}/", doc_id);
    let store = server_state.store.as_ref().unwrap();

    let file_infos = store
        .list(&prefix)
        .await
        .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

    // Convert the raw file info into the API response format. `info.key`
    // is the full storage key (e.g. `files/<doc_id>/<hash>`); the API
    // returns just the hash.
    let files = file_infos
        .into_iter()
        .map(|info| FileHistoryEntry {
            hash: info.key.rsplit('/').next().unwrap_or(&info.key).to_string(),
            size: info.size,
            created_at: info.last_modified,
        })
        .collect();

    Ok(Json(FileHistoryResponse { files }))
}

async fn handle_doc_versions(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<Json<DocumentVersionResponse>, AppError> {
    let token = get_token_from_header(auth_header);

    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            let auth = authenticator
                .verify_doc_token(token, &doc_id, current_time_epoch_millis())
                .map_err(|e| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token: {}", e),
                        "invalid_token",
                    )
                })?;

            if !matches!(auth, Authorization::ReadOnly | Authorization::Full) {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Insufficient permissions to view document versions"),
                    "insufficient_permissions",
                ));
            }
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    }

    let store = match &server_state.store {
        Some(s) => s,
        None => {
            return Err(AppError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                anyhow!("No store configured for operations"),
            ))
        }
    };

    let key = format!("{}/data.ysweet", doc_id);
    let versions = store
        .list_versions(&key)
        .await
        .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

    let entries = versions
        .into_iter()
        .map(|v| DocumentVersionEntry {
            version_id: v.version_id,
            created_at: v.last_modified,
            is_latest: v.is_latest,
        })
        .collect();

    Ok(Json(DocumentVersionResponse { versions: entries }))
}

async fn handle_file_head(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<StatusCode, AppError> {
    // Get token
    let token = get_token_from_header(auth_header);

    // Verify token is for this doc_id
    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // Verify token is for this doc_id
            let auth = authenticator
                .verify_file_token_for_doc(token, &doc_id, current_time_epoch_millis())
                .map_err(|e| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token: {}", e),
                        "invalid_token",
                    )
                })?;

            // Both ReadOnly and Full can check if a file exists
            if !matches!(auth, Authorization::ReadOnly | Authorization::Full) {
                return Err(AppError::auth(
                    StatusCode::FORBIDDEN,
                    anyhow!("Insufficient permissions to access file"),
                    "insufficient_permissions",
                ));
            }

            // Verify the token and get the file hash
            let permission = authenticator
                .verify_token_auto(token, current_time_epoch_millis())
                .map_err(|_| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token"),
                        "invalid_token",
                    )
                })?;

            if let Permission::File(file_permission) = permission {
                let file_hash = file_permission.file_hash;

                // Validate the file hash
                if !validate_file_hash(&file_hash) {
                    return Err(AppError::new(
                        StatusCode::BAD_REQUEST,
                        anyhow!("Invalid file hash format in token"),
                    ));
                }

                // Check if we have a store configured
                if server_state.store.is_none() {
                    return Err(AppError::new(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        anyhow!("No store configured for file operations"),
                    ));
                }

                // Construct the file path with proper format - using doc_id/file_hash
                let key = format!("files/{}/{}", doc_id, file_hash);

                // Check if the file exists with a direct call to S3
                let exists = server_state
                    .store
                    .as_ref()
                    .unwrap()
                    .exists(&key)
                    .await
                    .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

                if exists {
                    tracing::debug!("File exists: {}/{}", doc_id, file_hash);
                    return Ok(StatusCode::OK);
                } else {
                    tracing::debug!("File not found: {}/{}", doc_id, file_hash);
                    return Err(AppError::new(
                        StatusCode::NOT_FOUND,
                        anyhow!("File not found"),
                    ));
                }
            } else {
                return Err(AppError::new(
                    StatusCode::BAD_REQUEST,
                    anyhow!("Token is not a file token"),
                ));
            }
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    } else {
        // No auth configured
        return Err(AppError::auth(
            StatusCode::UNAUTHORIZED,
            anyhow!("Authentication is required for file operations"),
            "no_authenticator",
        ));
    }
}

async fn reload_webhook_config_endpoint(
    State(server_state): State<Arc<Server>>,
    auth_header: Option<TypedHeader<headers::Authorization<headers::authorization::Bearer>>>,
) -> Result<Json<Value>, AppError> {
    // Get token
    let token = get_token_from_header(auth_header);

    // Verify token is server token (for server admin operations)
    if let Some(authenticator) = &server_state.authenticator {
        if let Some(token) = token.as_deref() {
            // Verify this is a server admin token
            authenticator
                .verify_server_token(token, current_time_epoch_millis())
                .map_err(|e| {
                    AppError::auth(
                        StatusCode::UNAUTHORIZED,
                        anyhow!("Invalid token: {}", e),
                        "invalid_token",
                    )
                })?;
        } else {
            return Err(AppError::auth(
                StatusCode::UNAUTHORIZED,
                anyhow!("No token provided"),
                "missing_token",
            ));
        }
    }

    // Reload webhook configuration
    match server_state.reload_webhook_config().await {
        Ok(status) => Ok(Json(json!({
            "status": "success",
            "message": status
        }))),
        Err(e) => {
            tracing::error!("Failed to reload webhook config: {}", e);
            Err(AppError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                anyhow!("Failed to reload webhook configuration: {}", e),
            ))
        }
    }
}

async fn metrics_endpoint(State(_server_state): State<Arc<Server>>) -> Result<String, AppError> {
    use prometheus::{Encoder, TextEncoder};

    let encoder = TextEncoder::new();
    let metric_families = prometheus::gather();
    let mut buffer = Vec::new();

    encoder.encode(&metric_families, &mut buffer).map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            anyhow!("Failed to encode metrics: {}", e),
        )
    })?;

    Ok(String::from_utf8(buffer).map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            anyhow!("Failed to convert metrics to string: {}", e),
        )
    })?)
}

#[cfg(test)]
mod test {
    use super::*;
    use y_sweet_core::api_types::Authorization;
    use y_sweet_core::auth::ExpirationTimeEpochMillis;

    #[tokio::test]
    async fn test_auth_doc() {
        let server_state = Server::new(
            None,
            Duration::from_secs(60),
            None,
            None,
            vec![],
            CancellationToken::new(),
            true,
            None,
        )
        .await
        .unwrap();

        let doc_id = server_state.create_doc().await.unwrap();

        let token = auth_doc(
            None,
            TypedHeader(headers::Host::from(http::uri::Authority::from_static(
                "localhost",
            ))),
            State(Arc::new(server_state)),
            Path(doc_id.clone()),
            Some(Json(AuthDocRequest {
                authorization: Authorization::Full,
                user_id: None,
                valid_for_seconds: None,
            })),
        )
        .await
        .unwrap();

        let expected_url = format!("ws://localhost/d/{doc_id}/ws");
        assert_eq!(token.url, expected_url);
        assert_eq!(token.doc_id, doc_id);
        assert!(token.token.is_none());
    }

    #[tokio::test]
    async fn test_auth_doc_with_prefix() {
        let prefix: Url = "https://foo.bar".parse().unwrap();
        let server_state = Server::new(
            None,
            Duration::from_secs(60),
            None,
            Some(prefix),
            vec![],
            CancellationToken::new(),
            true,
            None,
        )
        .await
        .unwrap();

        let doc_id = server_state.create_doc().await.unwrap();

        let token = auth_doc(
            None,
            TypedHeader(headers::Host::from(http::uri::Authority::from_static(
                "localhost",
            ))),
            State(Arc::new(server_state)),
            Path(doc_id.clone()),
            None,
        )
        .await
        .unwrap();

        let expected_url = format!("wss://foo.bar/d/{doc_id}/ws");
        assert_eq!(token.url, expected_url);
        assert_eq!(token.doc_id, doc_id);
        assert!(token.token.is_none());
    }

    #[tokio::test]
    async fn test_websocket_auth_rejects_missing_token_when_auth_configured() {
        let authenticator = y_sweet_core::auth::Authenticator::gen_key().unwrap();
        let server_state = Arc::new(
            Server::new(
                None,
                Duration::from_secs(60),
                Some(authenticator),
                None,
                vec![],
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        let err = verify_socket_token(&server_state, "test-doc", None).unwrap_err();

        assert_eq!(err.status, StatusCode::UNAUTHORIZED);
        assert_eq!(err.auth_error_type, Some("missing_token"));
    }

    #[tokio::test]
    async fn test_websocket_auth_reports_expired_token_distinctly_from_invalid_token() {
        // #75491f2f recurrence diagnosis (2026-09-17): verify_socket_token used to
        // hardcode "invalid_token" for every WS-upgrade auth failure regardless of
        // cause, collapsing "expired" (clock-skew / late reconnect) and genuinely
        // malformed/forged tokens into one indistinguishable Prometheus series.
        let mut authenticator = y_sweet_core::auth::Authenticator::gen_key().unwrap();
        // Required for the CWT branch of verify_token_with_channel to actually
        // validate rather than short-circuit to a generic AuthError::InvalidToken
        // (see its `else` arm) — mirrors how the control-plane always configures
        // server.url / RELAY_AUDIENCE in practice.
        authenticator.set_expected_audience(Some("https://test.example.com".to_string()));
        let server_state = Arc::new(
            Server::new(
                None,
                Duration::from_secs(60),
                Some(authenticator.clone()),
                None,
                vec![],
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        let expired_time = ExpirationTimeEpochMillis(current_time_epoch_millis() - 5_000);
        let expired_token = authenticator
            .gen_doc_token_cwt("test-doc", Authorization::Full, expired_time, None, None)
            .unwrap();

        let err = verify_socket_token(&server_state, "test-doc", Some(&expired_token)).unwrap_err();

        assert_eq!(err.status, StatusCode::UNAUTHORIZED);
        assert_eq!(err.auth_error_type, Some("expired"));
    }

    /// Observations in `(lo, hi]` of the (process-global) overshoot histogram,
    /// read from ONE `gather()` snapshot. Two separate gathers (one per bound)
    /// are not atomic: a parallel test recording between them shifts one bound
    /// and makes the window read wrong or underflow.
    fn overshoot_observed_in(lo: f64, hi: f64) -> u64 {
        let families = prometheus::default_registry().gather();
        let Some(metric) = families
            .iter()
            .find(|f| f.get_name() == "relay_server_ws_token_expired_overshoot_seconds")
            .and_then(|f| f.get_metric().first())
        else {
            return 0;
        };
        let cumulative = |le: f64| {
            metric
                .get_histogram()
                .get_bucket()
                .iter()
                .find(|b| b.get_upper_bound() == le)
                .map(|b| b.get_cumulative_count())
                .unwrap_or(0)
        };
        cumulative(hi).saturating_sub(cumulative(lo))
    }

    #[tokio::test]
    async fn test_websocket_expired_token_records_overshoot_seconds() {
        // #dd739c96: on the `expired` branch, record now - exp so the three causes of
        // #cde2a0b7 (TTL-boundary race / stale cached token / clock skew) can be told apart.
        let mut authenticator = y_sweet_core::auth::Authenticator::gen_key().unwrap();
        authenticator.set_expected_audience(Some("https://test.example.com".to_string()));
        let server_state = Arc::new(
            Server::new(
                None,
                Duration::from_secs(60),
                Some(authenticator.clone()),
                None,
                vec![],
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        // Registry is process-global and other tests also reject expired tokens, so
        // assert on deltas over bucket WINDOWS, not on cumulative `<= le` counts.
        let mint = |age_ms: u64| {
            authenticator
                .gen_doc_token_cwt(
                    "test-doc",
                    Authorization::Full,
                    ExpirationTimeEpochMillis(current_time_epoch_millis() - age_ms),
                    None,
                    None,
                )
                .unwrap()
        };

        // Buckets are cumulative, so a bare `<= le` delta also picks up whatever
        // another parallel test records; a (lo, hi] window only sees values that
        // really are in that range. The sibling test above rejects a 5s-old token
        // (overshoot >= 5), which sits outside both windows below.
        let observed_in = overshoot_observed_in;

        let before = observed_in(2.0, 5.0);
        let err = verify_socket_token(&server_state, "test-doc", Some(&mint(3_000))).unwrap_err();
        assert_eq!(err.auth_error_type, Some("expired"));
        assert!(
            observed_in(2.0, 5.0) > before,
            "a token expired 3s ago must be observed in the (2, 5] bucket"
        );

        let before = observed_in(300.0, 3600.0);
        let err = verify_socket_token(&server_state, "test-doc", Some(&mint(400_000))).unwrap_err();
        assert_eq!(err.auth_error_type, Some("expired"));
        assert_eq!(
            observed_in(300.0, 3600.0),
            before + 1,
            "a token expired 400s ago must be observed in the (300, 3600] bucket"
        );
    }

    #[tokio::test]
    async fn test_websocket_auth_allows_missing_token_without_authenticator() {
        let server_state = Arc::new(
            Server::new(
                None,
                Duration::from_secs(60),
                None,
                None,
                vec![],
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        let (authorization, channel, user) =
            verify_socket_token(&server_state, "test-doc", None).unwrap();

        assert_eq!(authorization, Authorization::Full);
        assert_eq!(channel, None);
        assert_eq!(user, None);
    }

    #[tokio::test]
    async fn test_read_only_socket_access_allows_persisted_unloaded_doc() {
        use async_trait::async_trait;
        use std::collections::HashSet;
        use y_sweet_core::store::Result as StoreResult;

        struct ExistingDocStore {
            existing_keys: HashSet<String>,
        }

        #[async_trait]
        impl Store for ExistingDocStore {
            async fn init(&self) -> StoreResult<()> {
                Ok(())
            }

            async fn get(&self, _key: &str) -> StoreResult<Option<Vec<u8>>> {
                Ok(None)
            }

            async fn set(&self, _key: &str, _value: Vec<u8>) -> StoreResult<()> {
                Ok(())
            }

            async fn remove(&self, _key: &str) -> StoreResult<()> {
                Ok(())
            }

            async fn exists(&self, key: &str) -> StoreResult<bool> {
                Ok(self.existing_keys.contains(key))
            }
        }

        let doc_id = "persisted-doc";
        let store = ExistingDocStore {
            existing_keys: HashSet::from([format!("{}/data.ysweet", doc_id)]),
        };
        let server = Server::new(
            Some(Box::new(store)),
            Duration::from_secs(60),
            None,
            None,
            vec![],
            CancellationToken::new(),
            true,
            None,
        )
        .await
        .unwrap();

        assert!(!server.docs.contains_key(doc_id));
        server
            .ensure_socket_doc_access(doc_id, Authorization::ReadOnly)
            .await
            .unwrap();

        let err = server
            .ensure_socket_doc_access("missing-doc", Authorization::ReadOnly)
            .await
            .unwrap_err();
        assert_eq!(err.status, StatusCode::NOT_FOUND);

        server
            .ensure_socket_doc_access("new-doc", Authorization::Full)
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn test_file_head_endpoint() {
        use async_trait::async_trait;
        use std::collections::HashMap;
        use std::sync::Arc;
        use y_sweet_core::store::Result as StoreResult;

        // Create a mock store for testing
        #[derive(Clone)]
        struct MockStore {
            files: Arc<HashMap<String, Vec<u8>>>,
        }

        #[async_trait]
        impl Store for MockStore {
            async fn init(&self) -> StoreResult<()> {
                Ok(())
            }

            async fn get(&self, key: &str) -> StoreResult<Option<Vec<u8>>> {
                Ok(self.files.get(key).cloned())
            }

            async fn set(&self, _key: &str, _value: Vec<u8>) -> StoreResult<()> {
                Ok(())
            }

            async fn remove(&self, _key: &str) -> StoreResult<()> {
                Ok(())
            }

            async fn exists(&self, key: &str) -> StoreResult<bool> {
                Ok(self.files.contains_key(key))
            }

            async fn generate_upload_url(
                &self,
                _key: &str,
                _content_type: Option<&str>,
                _content_length: Option<u64>,
            ) -> StoreResult<Option<String>> {
                Ok(Some("http://mock-upload-url".to_string()))
            }

            async fn generate_download_url(&self, _key: &str) -> StoreResult<Option<String>> {
                Ok(Some("http://mock-download-url".to_string()))
            }
        }

        // Create a mock authenticator
        let mut authenticator = y_sweet_core::auth::Authenticator::gen_key().unwrap();
        authenticator.set_expected_audience(Some("https://api.example.com".to_string()));
        let doc_id = "test-doc-123";
        let file_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890";

        // Generate a file token
        let token = authenticator
            .gen_file_token_cwt(
                file_hash,
                doc_id,
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX), // Never expires for test
                None,
                None,
                None,
                None, // channel
            )
            .unwrap();

        // Set up the mock store with the test file
        let mut mock_files = HashMap::new();
        mock_files.insert(format!("files/{}/{}", doc_id, file_hash), vec![1, 2, 3, 4]);

        let mock_store = MockStore {
            files: Arc::new(mock_files),
        };

        // Create the server with our mock components
        let server_state = Arc::new(
            Server::new(
                Some(Box::new(mock_store)),
                Duration::from_secs(60),
                Some(authenticator.clone()),
                None,
                vec![],
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        // Create auth header with token
        let headers = TypedHeader(headers::Authorization::bearer(&token).unwrap());

        // Test the HEAD endpoint - should return 200 OK for existing file
        let result = handle_file_head(
            State(server_state.clone()),
            Path(doc_id.to_string()),
            Some(headers.clone()),
        )
        .await;

        assert!(
            result.is_ok(),
            "HEAD request should succeed for existing file"
        );
        assert_eq!(result.unwrap(), StatusCode::OK);

        // Test a file that doesn't exist
        let nonexistent_file_hash =
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";
        let nonexistent_token = authenticator
            .gen_file_token_cwt(
                nonexistent_file_hash,
                doc_id,
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
                None,
                None, // channel
            )
            .unwrap();

        let nonexistent_headers =
            TypedHeader(headers::Authorization::bearer(&nonexistent_token).unwrap());

        let result = handle_file_head(
            State(server_state),
            Path(doc_id.to_string()),
            Some(nonexistent_headers),
        )
        .await;

        assert!(
            result.is_err(),
            "HEAD request should fail for non-existent file"
        );
        match result {
            Err(ref e) => assert_eq!(e.status, StatusCode::NOT_FOUND),
            _ => panic!("Expected NOT_FOUND status for non-existent file"),
        };
    }

    #[tokio::test]
    async fn test_generate_context_aware_urls_with_prefix() {
        let url: Url = "https://api.example.com".parse().unwrap();
        let allowed_hosts = vec![];
        let doc_id = "test-doc";

        let (ws_url, base_url) =
            generate_context_aware_urls(&Some(url), &allowed_hosts, "unused-host", doc_id).unwrap();

        assert_eq!(ws_url, "wss://api.example.com/d/test-doc/ws");
        assert_eq!(base_url, "https://api.example.com/d/test-doc");
    }

    #[tokio::test]
    async fn test_generate_context_aware_urls_with_allowed_hosts() {
        let allowed_hosts = vec![
            AllowedHost {
                host: "api.example.com".to_string(),
                scheme: "https".to_string(),
            },
            AllowedHost {
                host: "app.flycast".to_string(),
                scheme: "http".to_string(),
            },
        ];
        let doc_id = "test-doc";

        // Test HTTPS host
        let (ws_url, base_url) =
            generate_context_aware_urls(&None, &allowed_hosts, "api.example.com", doc_id).unwrap();

        assert_eq!(ws_url, "wss://api.example.com/d/test-doc/ws");
        assert_eq!(base_url, "https://api.example.com/d/test-doc");

        // Test flycast host
        let (ws_url, base_url) =
            generate_context_aware_urls(&None, &allowed_hosts, "app.flycast", doc_id).unwrap();

        assert_eq!(ws_url, "ws://app.flycast/d/test-doc/ws");
        assert_eq!(base_url, "http://app.flycast/d/test-doc");
    }

    #[tokio::test]
    async fn test_generate_context_aware_urls_rejects_unknown_host() {
        let allowed_hosts = vec![AllowedHost {
            host: "api.example.com".to_string(),
            scheme: "https".to_string(),
        }];
        let doc_id = "test-doc";

        let result = generate_context_aware_urls(&None, &allowed_hosts, "malicious.host", doc_id);

        assert!(result.is_err());
        match result {
            Err(ref e) if e.status == StatusCode::BAD_REQUEST => {} // Expected
            _ => panic!("Expected BAD_REQUEST for unknown host"),
        }
    }

    #[tokio::test]
    async fn test_auth_doc_with_context_aware_urls() {
        let allowed_hosts = vec![
            AllowedHost {
                host: "api.example.com".to_string(),
                scheme: "https".to_string(),
            },
            AllowedHost {
                host: "app.flycast".to_string(),
                scheme: "http".to_string(),
            },
        ];

        let server_state = Arc::new(
            Server::new(
                None,
                Duration::from_secs(60),
                None,
                None, // No URL prefix - use context-aware generation
                allowed_hosts.clone(),
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        let doc_id = server_state.create_doc().await.unwrap();

        // Test with HTTPS host
        let token = auth_doc(
            None,
            TypedHeader(headers::Host::from(http::uri::Authority::from_static(
                "api.example.com",
            ))),
            State(server_state.clone()),
            Path(doc_id.clone()),
            Some(Json(AuthDocRequest {
                authorization: Authorization::Full,
                user_id: None,
                valid_for_seconds: None,
            })),
        )
        .await
        .unwrap();

        assert_eq!(token.url, format!("wss://api.example.com/d/{}/ws", doc_id));
        assert_eq!(
            token.base_url,
            Some(format!("https://api.example.com/d/{}", doc_id))
        );

        // Test with flycast host - create another server instance with same allowed hosts
        let server_state2 = Arc::new(
            Server::new(
                None,
                Duration::from_secs(60),
                None,
                None,
                allowed_hosts,
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        server_state2.load_doc(&doc_id, None).await.unwrap();

        let token = auth_doc(
            None,
            TypedHeader(headers::Host::from(http::uri::Authority::from_static(
                "app.flycast",
            ))),
            State(server_state2),
            Path(doc_id.clone()),
            Some(Json(AuthDocRequest {
                authorization: Authorization::Full,
                user_id: None,
                valid_for_seconds: None,
            })),
        )
        .await
        .unwrap();

        assert_eq!(token.url, format!("ws://app.flycast/d/{}/ws", doc_id));
        assert_eq!(
            token.base_url,
            Some(format!("http://app.flycast/d/{}", doc_id))
        );
    }

    #[tokio::test]
    async fn test_file_upload_url_with_filesystem_store() {
        use crate::stores::filesystem::FileSystemStore;
        use tempfile::TempDir;
        use y_sweet_core::api_types::Authorization;
        use y_sweet_core::auth::{Authenticator, ExpirationTimeEpochMillis};

        // Create a test authenticator
        let mut authenticator = Authenticator::gen_key().unwrap();
        authenticator.set_expected_audience(Some("https://api.example.com".to_string()));

        let allowed_hosts = vec![AllowedHost {
            host: "api.example.com".to_string(),
            scheme: "https".to_string(),
        }];

        // Create filesystem store
        let temp_dir = TempDir::new().unwrap();
        let store = FileSystemStore::new(temp_dir.path().to_path_buf()).unwrap();

        let server_state = Arc::new(
            Server::new(
                Some(Box::new(store)),
                Duration::from_secs(60),
                Some(authenticator.clone()),
                None,
                allowed_hosts,
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        let doc_id = "test-doc";
        let file_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890";

        // Generate a file token
        let token = authenticator
            .gen_file_token_cwt(
                file_hash,
                doc_id,
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("image/png"),
                Some(1024),
                None,
                None,
            )
            .unwrap();

        // Test upload URL generation
        let host_header = TypedHeader(headers::Host::from(http::uri::Authority::from_static(
            "api.example.com",
        )));
        let auth_header = Some(TypedHeader(headers::Authorization::bearer(&token).unwrap()));

        let result = handle_file_upload_url(
            State(server_state),
            Path(doc_id.to_string()),
            host_header,
            auth_header,
        )
        .await
        .unwrap();

        let Json(response) = result;
        // Should get full HTTPS URL with token
        assert!(response
            .upload_url
            .starts_with("https://api.example.com/f/"));
        assert!(response
            .upload_url
            .contains(&format!("/f/{}/upload", doc_id)));
        assert!(response.upload_url.contains(&format!("token={}", token)));
    }

    #[tokio::test]
    async fn test_file_download_url_with_filesystem_store() {
        use crate::stores::filesystem::FileSystemStore;
        use tempfile::TempDir;
        use y_sweet_core::api_types::Authorization;
        use y_sweet_core::auth::{Authenticator, ExpirationTimeEpochMillis};

        // Create a test authenticator
        let mut authenticator = Authenticator::gen_key().unwrap();
        authenticator.set_expected_audience(Some("http://localhost".to_string()));

        let allowed_hosts = vec![AllowedHost {
            host: "localhost".to_string(),
            scheme: "http".to_string(),
        }];

        // Create filesystem store
        let temp_dir = TempDir::new().unwrap();
        let store = FileSystemStore::new(temp_dir.path().to_path_buf()).unwrap();

        let server_state = Arc::new(
            Server::new(
                Some(Box::new(store)),
                Duration::from_secs(60),
                Some(authenticator.clone()),
                None,
                allowed_hosts,
                CancellationToken::new(),
                true,
                None,
            )
            .await
            .unwrap(),
        );

        let doc_id = "test-doc";
        let file_hash = "def456789012345678901234567890def456789012345678901234567890def4";

        // Generate a file token
        let token = authenticator
            .gen_file_token_cwt(
                file_hash,
                doc_id,
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("image/jpeg"),
                Some(2048),
                None,
                None,
            )
            .unwrap();

        // Test download URL generation
        let host_header = TypedHeader(headers::Host::from(http::uri::Authority::from_static(
            "localhost",
        )));
        let auth_header = Some(TypedHeader(headers::Authorization::bearer(&token).unwrap()));

        let result = handle_file_download_url(
            State(server_state),
            Path(doc_id.to_string()),
            host_header,
            Query(FileDownloadQueryParams { hash: None }),
            auth_header,
        )
        .await
        .unwrap();

        let Json(response) = result;
        // Should get full HTTP URL with hash and token
        assert!(response.download_url.starts_with("http://localhost/f/"));
        assert!(response
            .download_url
            .contains(&format!("/f/{}/download", doc_id)));
        assert!(response
            .download_url
            .contains(&format!("hash={}", file_hash)));
        assert!(response.download_url.contains(&format!("token={}", token)));
    }

    /// Test that persistence workers terminate when docs are garbage collected.
    /// This is a regression test for the memory leak fixed in PR #401.
    #[tokio::test]
    async fn test_persistence_worker_terminates_on_gc() {
        // Use a very short checkpoint frequency to speed up the test
        let checkpoint_freq = Duration::from_millis(50);

        let server = Arc::new(
            Server::new(
                None,
                checkpoint_freq,
                None,
                None,
                vec![],
                CancellationToken::new(),
                true, // doc_gc enabled
                None,
            )
            .await
            .unwrap(),
        );

        // Create a doc - this spawns persistence and GC workers
        let doc_id = server.create_doc().await.unwrap();

        // Verify the doc exists
        assert!(server.docs.contains_key(&doc_id));

        // The doc has no external references (we're not holding an awareness Arc),
        // so it should be eligible for GC after 2 checkpoint intervals.
        // Wait for GC to happen (2 intervals + some buffer)
        tokio::time::sleep(checkpoint_freq * 5).await;

        // Doc should be removed by GC
        assert!(
            !server.docs.contains_key(&doc_id),
            "Doc should have been garbage collected"
        );

        // Close the tracker and wait for all workers to finish.
        // If persistence workers don't terminate (the bug), this will hang.
        server.doc_worker_tracker.close();

        let wait_result =
            tokio::time::timeout(Duration::from_secs(2), server.doc_worker_tracker.wait()).await;

        assert!(
            wait_result.is_ok(),
            "Persistence workers should terminate after GC, but they hung"
        );
    }

    /// Regression test for TR-33: the dirty-signal callback used to call
    /// `.unwrap()` on `try_send`, panicking as soon as the receiving
    /// persistence worker was gone and the channel closed. Cancelling the
    /// server and draining the worker tracker guarantees the channel is
    /// closed before we trigger one more doc update.
    #[tokio::test]
    async fn test_dirty_callback_does_not_panic_on_closed_channel() {
        use yrs::{Text, Transact};

        let cancellation_token = CancellationToken::new();
        let server = Arc::new(
            Server::new(
                None,
                Duration::from_secs(60),
                None,
                None,
                vec![],
                cancellation_token.clone(),
                false, // doc_gc disabled - not relevant to this test
                None,
            )
            .await
            .unwrap(),
        );

        let doc_id = server.create_doc().await.unwrap();
        let awareness = server.get_or_create_doc(&doc_id).await.unwrap().awareness();

        // Terminate the persistence worker so its Receiver<()> is dropped,
        // closing the dirty-signal channel.
        cancellation_token.cancel();
        server.doc_worker_tracker.close();
        tokio::time::timeout(Duration::from_secs(2), server.doc_worker_tracker.wait())
            .await
            .expect("persistence worker should terminate after cancellation");

        // Triggering another update now invokes the dirty callback against a
        // closed channel. Before the fix this panicked inside
        // `send.try_send(()).unwrap()`.
        let guard = awareness.read().unwrap();
        let text = guard.doc().get_or_insert_text("body");
        let mut txn = guard.doc().transact_mut();
        text.insert(&mut txn, 0, "trigger dirty callback on closed channel");
    }
}

async fn handle_file_upload(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    Query(params): Query<FileUploadParams>,
    mut multipart: Multipart,
) -> Result<StatusCode, AppError> {
    tracing::info!(doc_id = %doc_id, "Handling file upload");

    let permission = validate_file_token(&server_state, &params.token, &doc_id)?;

    if let Permission::File(file_permission) = permission {
        // Only allow Full permission to upload
        if !matches!(file_permission.authorization, Authorization::Full) {
            return Err(AppError::auth(
                StatusCode::FORBIDDEN,
                anyhow!("Insufficient permissions to upload files"),
                "insufficient_permissions",
            ));
        }

        // Get file field from multipart stream
        let field = multipart
            .next_field()
            .await
            .map_err(|e| AppError::new(StatusCode::BAD_REQUEST, e.into()))?
            .ok_or_else(|| AppError::new(StatusCode::BAD_REQUEST, anyhow!("No file provided")))?;

        // Validate content-type if specified in token
        if let Some(expected_type) = &file_permission.content_type {
            if field.content_type() != Some(expected_type) {
                return Err(AppError::new(
                    StatusCode::BAD_REQUEST,
                    anyhow!("Content-Type mismatch: expected {}", expected_type),
                ));
            }
        }

        // Check if we have a store configured
        let store = server_state.store.as_ref().ok_or_else(|| {
            AppError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                anyhow!("No store configured for file uploads"),
            )
        })?;

        // Prepare for streaming validation
        let key = format!("files/{}/{}", doc_id, file_permission.file_hash);

        // Create a temporary file for atomic writes
        let temp_file = NamedTempFile::new()
            .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

        let mut hasher = Sha256::new();
        let mut total_size = 0u64;
        let mut file_writer = temp_file.as_file();

        // Stream chunks while validating
        let mut stream = field.into_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|e| AppError::new(StatusCode::BAD_REQUEST, e.into()))?;

            // Update hash and size
            hasher.update(&chunk);
            total_size += chunk.len() as u64;

            // Early size validation
            if let Some(expected_length) = file_permission.content_length {
                if total_size > expected_length {
                    return Err(AppError::new(
                        StatusCode::PAYLOAD_TOO_LARGE,
                        anyhow!("File exceeds expected size"),
                    ));
                }
            }

            // Write to temp file
            file_writer
                .write_all(&chunk)
                .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;
        }

        // Final validations
        if let Some(expected_length) = file_permission.content_length {
            if total_size != expected_length {
                return Err(AppError::new(
                    StatusCode::BAD_REQUEST,
                    anyhow!(
                        "Content-Length mismatch: expected {}, got {}",
                        expected_length,
                        total_size
                    ),
                ));
            }
        }

        let actual_hash = format!("{:x}", hasher.finalize());
        if actual_hash != file_permission.file_hash {
            return Err(AppError::new(
                StatusCode::BAD_REQUEST,
                anyhow!(
                    "File hash mismatch: expected {}, got {}",
                    file_permission.file_hash,
                    actual_hash
                ),
            ));
        }

        // Read the temp file contents and store using the store interface
        let file_contents = std::fs::read(temp_file.path())
            .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

        store
            .set(&key, file_contents)
            .await
            .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

        Ok(StatusCode::OK)
    } else {
        Err(AppError::new(
            StatusCode::BAD_REQUEST,
            anyhow!("Invalid permission type"),
        ))
    }
}

async fn handle_file_upload_raw(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    Query(params): Query<FileUploadParams>,
    body: axum::body::Bytes,
) -> Result<StatusCode, AppError> {
    tracing::info!(doc_id = %doc_id, "Handling raw file upload");

    let permission = validate_file_token(&server_state, &params.token, &doc_id)?;

    if let Permission::File(file_permission) = permission {
        // Only allow Full permission to upload
        if !matches!(file_permission.authorization, Authorization::Full) {
            return Err(AppError::auth(
                StatusCode::FORBIDDEN,
                anyhow!("Insufficient permissions to upload files"),
                "insufficient_permissions",
            ));
        }

        // Check if we have a store configured
        let store = server_state.store.as_ref().ok_or_else(|| {
            AppError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                anyhow!("No store configured for file uploads"),
            )
        })?;

        let key = format!("files/{}/{}", doc_id, file_permission.file_hash);

        // Validate content length if specified in token
        if let Some(expected_length) = file_permission.content_length {
            if body.len() as u64 != expected_length {
                return Err(AppError::new(
                    StatusCode::BAD_REQUEST,
                    anyhow!(
                        "Content-Length mismatch: expected {}, got {}",
                        expected_length,
                        body.len()
                    ),
                ));
            }
        }

        // Validate file hash
        let mut hasher = Sha256::new();
        hasher.update(&body);
        let actual_hash = format!("{:x}", hasher.finalize());

        if actual_hash != file_permission.file_hash {
            return Err(AppError::new(
                StatusCode::BAD_REQUEST,
                anyhow!(
                    "File hash mismatch: expected {}, got {}",
                    file_permission.file_hash,
                    actual_hash
                ),
            ));
        }

        // Store the file
        store
            .set(&key, body.to_vec())
            .await
            .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?;

        Ok(StatusCode::OK)
    } else {
        Err(AppError::new(
            StatusCode::BAD_REQUEST,
            anyhow!("Invalid permission type"),
        ))
    }
}

async fn handle_file_download(
    State(server_state): State<Arc<Server>>,
    Path(doc_id): Path<String>,
    Query(params): Query<FileDownloadParams>,
) -> Result<Response, AppError> {
    tracing::info!(doc_id = %doc_id, hash = %params.hash, "Handling file download");

    let permission = validate_file_token(&server_state, &params.token, &doc_id)?;

    if let Permission::File(file_permission) = permission {
        // Both ReadOnly and Full can download files
        if !matches!(
            file_permission.authorization,
            Authorization::ReadOnly | Authorization::Full
        ) {
            return Err(AppError::auth(
                StatusCode::FORBIDDEN,
                anyhow!("Insufficient permissions to download file"),
                "insufficient_permissions",
            ));
        }

        // Verify the hash parameter matches the token
        if file_permission.file_hash != params.hash {
            return Err(AppError::new(
                StatusCode::BAD_REQUEST,
                anyhow!("Hash parameter does not match token"),
            ));
        }

        // Check if we have a store configured
        let store = server_state.store.as_ref().ok_or_else(|| {
            AppError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                anyhow!("No store configured for file downloads"),
            )
        })?;

        // Retrieve file
        let key = format!("files/{}/{}", doc_id, file_permission.file_hash);
        let file_data = store
            .get(&key)
            .await
            .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?
            .ok_or_else(|| AppError::new(StatusCode::NOT_FOUND, anyhow!("File not found")))?;

        // Stream response
        let content_type = file_permission
            .content_type
            .unwrap_or_else(|| "application/octet-stream".to_string());

        Ok(Response::builder()
            .status(StatusCode::OK)
            .header("content-type", content_type)
            .header("content-length", file_data.len())
            .body(axum::body::Body::from(file_data))
            .map_err(|e| AppError::new(StatusCode::INTERNAL_SERVER_ERROR, e.into()))?)
    } else {
        Err(AppError::new(
            StatusCode::BAD_REQUEST,
            anyhow!("Invalid permission type"),
        ))
    }
}
