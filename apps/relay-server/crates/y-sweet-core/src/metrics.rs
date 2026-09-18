use prometheus::{CounterVec, GaugeVec, Histogram, HistogramOpts, HistogramVec, Opts, Registry};
use std::sync::{Arc, OnceLock};

#[derive(Clone)]
pub struct RelayMetrics {
    // Webhook system metrics
    pub webhook_requests_total: CounterVec,
    pub webhook_request_duration_seconds: HistogramVec,
    pub webhook_queue_length: GaugeVec,
    pub webhook_active_dispatchers: GaugeVec,
    pub webhook_config_reloads_total: CounterVec,

    // Event system metrics
    pub events_created_total: CounterVec,
    pub events_dispatched_total: CounterVec,
    pub events_delivered_total: CounterVec,
    pub event_updates_merged_total: CounterVec,
    pub sync_protocol_connections: GaugeVec,
    pub sync_protocol_subscriptions_by_channel: GaugeVec,
    pub debounced_queue_length: GaugeVec,
    pub docs_active: GaugeVec,
    pub doc_connections_opened_total: CounterVec,
    pub doc_connections_closed_total: CounterVec,

    // Authentication & security metrics
    pub http_auth_errors_total: CounterVec,
    pub http_auth_success_total: CounterVec,
    pub ws_token_expired_overshoot_seconds: Histogram,

    // Object store metrics
    pub s3_requests_total: CounterVec,
}

static RELAY_METRICS: OnceLock<Result<Arc<RelayMetrics>, prometheus::Error>> = OnceLock::new();

impl RelayMetrics {
    pub fn new() -> Result<Arc<Self>, prometheus::Error> {
        match RELAY_METRICS.get_or_init(|| Self::new_with_registry(prometheus::default_registry()))
        {
            Ok(metrics) => Ok(metrics.clone()),
            Err(e) => Err(prometheus::Error::Msg(e.to_string())),
        }
    }

    pub fn new_with_registry(registry: &Registry) -> Result<Arc<Self>, prometheus::Error> {
        let webhook_requests_total = CounterVec::new(
            Opts::new(
                "relay_server_webhook_requests_total",
                "Total number of webhook requests sent",
            ),
            &["prefix", "status"],
        )?;
        registry.register(Box::new(webhook_requests_total.clone()))?;

        let webhook_request_duration_seconds = HistogramVec::new(
            HistogramOpts::new(
                "relay_server_webhook_request_duration_seconds",
                "Duration of webhook HTTP requests in seconds",
            ),
            &["prefix", "status"],
        )?;
        registry.register(Box::new(webhook_request_duration_seconds.clone()))?;

        let webhook_queue_length = GaugeVec::new(
            Opts::new(
                "relay_server_webhook_queue_length",
                "Current number of documents in webhook queues",
            ),
            &["prefix"],
        )?;
        registry.register(Box::new(webhook_queue_length.clone()))?;

        let webhook_active_dispatchers = GaugeVec::new(
            Opts::new(
                "relay_server_webhook_active_dispatchers",
                "Number of active webhook dispatchers",
            ),
            &["prefix"],
        )?;
        registry.register(Box::new(webhook_active_dispatchers.clone()))?;

        let webhook_config_reloads_total = CounterVec::new(
            Opts::new(
                "relay_server_webhook_config_reloads_total",
                "Total number of webhook configuration reloads",
            ),
            &["status"],
        )?;
        registry.register(Box::new(webhook_config_reloads_total.clone()))?;

        // Event system metrics
        let events_created_total = CounterVec::new(
            Opts::new(
                "relay_server_events_created_total",
                "Total number of events created",
            ),
            &["event_type"],
        )?;
        registry.register(Box::new(events_created_total.clone()))?;

        let events_dispatched_total = CounterVec::new(
            Opts::new(
                "relay_server_events_dispatched_total",
                "Total number of events dispatched to senders",
            ),
            &["event_type", "sender_type"],
        )?;
        registry.register(Box::new(events_dispatched_total.clone()))?;

        let events_delivered_total = CounterVec::new(
            Opts::new(
                "relay_server_events_delivered_total",
                "Total number of events successfully delivered",
            ),
            &["event_type", "transport"],
        )?;
        registry.register(Box::new(events_delivered_total.clone()))?;

        let event_updates_merged_total = CounterVec::new(
            Opts::new(
                "relay_server_event_updates_merged_total",
                "Total number of Yjs updates merged in events",
            ),
            &[], // No labels to avoid high cardinality
        )?;
        registry.register(Box::new(event_updates_merged_total.clone()))?;

        let sync_protocol_connections = GaugeVec::new(
            Opts::new(
                "relay_server_sync_protocol_connections_total",
                "Total number of active sync protocol connections across all documents",
            ),
            &[], // Aggregate across all documents
        )?;
        registry.register(Box::new(sync_protocol_connections.clone()))?;
        // Force the zero-label child into existence at startup. A GaugeVec with no
        // labels otherwise has no series at all until the first `set()` call, so
        // Prometheus scrapes report the metric family as absent — indistinguishable
        // from "no data" — right up until the first connection opens. That gap was
        // read as "zero data" instead of "zero connections" for 6+ months (#f3cb5365).
        sync_protocol_connections.with_label_values(&[]).set(0.0);

        let sync_protocol_subscriptions_by_channel = GaugeVec::new(
            Opts::new(
                "relay_server_sync_protocol_subscriptions_by_channel",
                "Number of sync protocol event subscriptions per channel",
            ),
            &["channel"], // Track subscriptions per channel/document
        )?;
        registry.register(Box::new(sync_protocol_subscriptions_by_channel.clone()))?;

        let debounced_queue_length = GaugeVec::new(
            Opts::new(
                "relay_server_debounced_queue_length",
                "Number of documents with pending debounced events",
            ),
            &["queue_type"],
        )?;
        registry.register(Box::new(debounced_queue_length.clone()))?;

        // Collaborative-editing surface metrics: aggregates only, no doc_id/user_id
        // labels. sync_protocol_subscriptions_by_channel (below) already carries
        // doc_id, and its cardinality is bounded by clear_doc_subscription() being
        // called whenever a document's connection count reaches zero — see that
        // method's doc comment for why an unbounded per-doc_id label is unsafe here.
        let docs_active = GaugeVec::new(
            Opts::new(
                "relay_server_docs_active_total",
                "Number of documents with at least one active sync protocol connection",
            ),
            &[], // Aggregate only — see doc_id cardinality note above
        )?;
        registry.register(Box::new(docs_active.clone()))?;
        // Same startup-visibility fix as sync_protocol_connections above (#f3cb5365):
        // force the zero-label child into existence so "0 active docs" is
        // observable from the first scrape, not indistinguishable from "no data".
        docs_active.with_label_values(&[]).set(0.0);

        let doc_connections_opened_total = CounterVec::new(
            Opts::new(
                "relay_server_doc_connections_opened_total",
                "Total number of document sync-protocol connections opened",
            ),
            &[],
        )?;
        registry.register(Box::new(doc_connections_opened_total.clone()))?;

        let doc_connections_closed_total = CounterVec::new(
            Opts::new(
                "relay_server_doc_connections_closed_total",
                "Total number of document sync-protocol connections closed (detected via weak-reference cleanup or explicit unregister)",
            ),
            &[],
        )?;
        registry.register(Box::new(doc_connections_closed_total.clone()))?;

        // Authentication & Security metrics
        let http_auth_errors_total = CounterVec::new(
            Opts::new(
                "relay_server_http_auth_errors_total",
                "Total number of HTTP authentication/authorization errors",
            ),
            &["error_type", "status_code", "path", "method"],
        )?;
        registry.register(Box::new(http_auth_errors_total.clone()))?;

        // Success-side counterpart to http_auth_errors_total, sharing the `path`/`method`
        // labels so a success rate can be computed with a single PromQL expression
        // (successes / (successes + errors)) instead of needing an external total-request
        // count as the denominator — without this, an error count alone can't be told apart
        // from "0 errors out of 0 requests" (the exact ambiguity that hid the CWT auth
        // defect, #f3cb5365).
        let http_auth_success_total = CounterVec::new(
            Opts::new(
                "relay_server_http_auth_success_total",
                "Total number of HTTP requests that passed authentication/authorization",
            ),
            &["path", "method"],
        )?;
        registry.register(Box::new(http_auth_success_total.clone()))?;

        // How far past `exp` a WS-upgrade token was when rejected as `expired`. Tells
        // apart (a) a TTL-boundary race (median ~1-2s), (b) a stale cached token after
        // sleep/network loss (long tail to minutes/hours) and (c) client/server clock
        // skew (a narrow peak at N s). Deliberately UNLABELED: no user/share/doc ids —
        // series count is fixed at one per bucket, however many clients connect.
        let ws_token_expired_overshoot_seconds = Histogram::with_opts(
            HistogramOpts::new(
                "relay_server_ws_token_expired_overshoot_seconds",
                "Seconds by which a WebSocket-upgrade token was past its expiry when rejected as expired",
            )
            .buckets(vec![1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0, 3600.0]),
        )?;
        registry.register(Box::new(ws_token_expired_overshoot_seconds.clone()))?;

        // Object store metrics
        let s3_requests_total = CounterVec::new(
            Opts::new(
                "relay_server_s3_requests_total",
                "Total S3-compatible object store requests, labeled by HTTP method and outcome",
            ),
            &["method", "outcome"],
        )?;
        registry.register(Box::new(s3_requests_total.clone()))?;

        Ok(Arc::new(Self {
            webhook_requests_total,
            webhook_request_duration_seconds,
            webhook_queue_length,
            webhook_active_dispatchers,
            webhook_config_reloads_total,
            events_created_total,
            events_dispatched_total,
            events_delivered_total,
            event_updates_merged_total,
            sync_protocol_connections,
            sync_protocol_subscriptions_by_channel,
            debounced_queue_length,
            docs_active,
            doc_connections_opened_total,
            doc_connections_closed_total,
            http_auth_errors_total,
            http_auth_success_total,
            ws_token_expired_overshoot_seconds,
            s3_requests_total,
        }))
    }

    #[cfg(test)]
    pub fn new_for_test() -> Result<Arc<Self>, prometheus::Error> {
        let registry = Registry::new();
        Self::new_with_registry(&registry)
    }

    pub fn record_webhook_request(&self, prefix: &str, status: &str, duration_seconds: f64) {
        self.webhook_requests_total
            .with_label_values(&[prefix, status])
            .inc();

        self.webhook_request_duration_seconds
            .with_label_values(&[prefix, status])
            .observe(duration_seconds);
    }

    pub fn set_queue_length(&self, prefix: &str, length: usize) {
        self.webhook_queue_length
            .with_label_values(&[prefix])
            .set(length as f64);
    }

    pub fn set_active_dispatchers(&self, prefix: &str, count: usize) {
        self.webhook_active_dispatchers
            .with_label_values(&[prefix])
            .set(count as f64);
    }

    pub fn record_config_reload(&self, status: &str) {
        self.webhook_config_reloads_total
            .with_label_values(&[status])
            .inc();
    }

    // Event system metrics methods
    pub fn record_event_created(&self, event_type: &str) {
        self.events_created_total
            .with_label_values(&[event_type])
            .inc();
    }

    pub fn record_event_dispatched(&self, event_type: &str, sender_type: &str) {
        self.events_dispatched_total
            .with_label_values(&[event_type, sender_type])
            .inc();
    }

    pub fn record_event_delivered(&self, event_type: &str, transport: &str) {
        self.events_delivered_total
            .with_label_values(&[event_type, transport])
            .inc();
    }

    pub fn record_updates_merged(&self, count: usize) {
        self.event_updates_merged_total
            .with_label_values(&[])
            .inc_by(count as f64);
    }

    pub fn set_sync_protocol_connections(&self, count: usize) {
        self.sync_protocol_connections
            .with_label_values(&[])
            .set(count as f64);
    }

    pub fn set_sync_protocol_subscriptions_by_channel(&self, channel: &str, count: usize) {
        self.sync_protocol_subscriptions_by_channel
            .with_label_values(&[channel])
            .set(count as f64);
    }

    /// Remove a document's series from sync_protocol_subscriptions_by_channel entirely,
    /// instead of setting it to 0. doc_id is an unbounded, user-generated UUID; leaving
    /// a permanent zero-value series behind for every document that has EVER had a
    /// connection (rather than dropping the series once it has none) grows this metric
    /// family forever and reproduces the exact cardinality-explosion class that took
    /// down control-plane's /metrics scrape (#4b0eac5c, 2254 label values, 7.44MB
    /// payload, scrape timeout). Measured live on prod 2026-08-20: 71 permanent
    /// zero-value channel series after ~11h uptime, using the pre-existing
    /// set(channel, 0) call sites this method replaces. Call this whenever a
    /// document's connection count reaches zero; cardinality is then bounded by the
    /// number of CURRENTLY active documents, not all-time-ever documents.
    pub fn clear_doc_subscription(&self, channel: &str) {
        // Best-effort: the series may already be gone (e.g. called twice for the same
        // zero-transition), which is not an error worth surfacing.
        let _ = self
            .sync_protocol_subscriptions_by_channel
            .remove_label_values(&[channel]);
    }

    pub fn set_debounced_queue_length(&self, queue_type: &str, length: usize) {
        self.debounced_queue_length
            .with_label_values(&[queue_type])
            .set(length as f64);
    }

    pub fn set_docs_active(&self, count: usize) {
        self.docs_active.with_label_values(&[]).set(count as f64);
    }

    pub fn record_doc_connection_opened(&self) {
        self.doc_connections_opened_total
            .with_label_values(&[])
            .inc();
    }

    pub fn record_doc_connections_closed(&self, count: usize) {
        self.doc_connections_closed_total
            .with_label_values(&[])
            .inc_by(count as f64);
    }

    // Authentication & Security metrics methods
    pub fn record_http_auth_error(
        &self,
        error_type: &str,
        status_code: &str,
        path: &str,
        method: &str,
    ) {
        self.http_auth_errors_total
            .with_label_values(&[error_type, status_code, path, method])
            .inc();
    }

    pub fn record_http_auth_success(&self, path: &str, method: &str) {
        self.http_auth_success_total
            .with_label_values(&[path, method])
            .inc();
    }

    pub fn record_ws_token_expired_overshoot(&self, overshoot_seconds: f64) {
        self.ws_token_expired_overshoot_seconds
            .observe(overshoot_seconds);
    }

    pub fn record_s3_request(&self, method: &str, outcome: &str) {
        self.s3_requests_total
            .with_label_values(&[method, outcome])
            .inc();
    }
}

impl Default for RelayMetrics {
    fn default() -> Self {
        Self::new()
            .expect("Failed to create relay metrics")
            .as_ref()
            .clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use prometheus::core::Collector;

    #[test]
    fn test_sync_protocol_subscription_metrics() {
        let metrics = RelayMetrics::new_for_test().unwrap();

        // Test setting subscriptions for different channels
        metrics.set_sync_protocol_subscriptions_by_channel("doc_123", 2);
        metrics.set_sync_protocol_subscriptions_by_channel("doc_456", 1);
        metrics.set_sync_protocol_subscriptions_by_channel("doc_789", 3);

        // Test removing subscriptions (setting to 0)
        metrics.set_sync_protocol_subscriptions_by_channel("doc_456", 0);

        // Test that we can call the metric method without panicking
        // (In real usage, these would be retrieved by Prometheus)
        assert!(true);
    }

    #[test]
    fn test_http_auth_error_metrics() {
        let metrics = RelayMetrics::new_for_test().unwrap();

        // Record various auth errors
        metrics.record_http_auth_error("invalid_signature", "401", "/doc/ws/:doc_id", "GET");
        metrics.record_http_auth_error("expired", "401", "/d/:doc_id/update", "POST");
        metrics.record_http_auth_error("missing_token", "401", "/doc/new", "POST");
        metrics.record_http_auth_error("prefix_mismatch", "403", "/doc/new", "POST");

        // Verify metrics were recorded
        let sig_failures = metrics
            .http_auth_errors_total
            .with_label_values(&["invalid_signature", "401", "/doc/ws/:doc_id", "GET"])
            .get();
        assert_eq!(sig_failures, 1.0);

        let expired = metrics
            .http_auth_errors_total
            .with_label_values(&["expired", "401", "/d/:doc_id/update", "POST"])
            .get();
        assert_eq!(expired, 1.0);

        let missing = metrics
            .http_auth_errors_total
            .with_label_values(&["missing_token", "401", "/doc/new", "POST"])
            .get();
        assert_eq!(missing, 1.0);

        let prefix = metrics
            .http_auth_errors_total
            .with_label_values(&["prefix_mismatch", "403", "/doc/new", "POST"])
            .get();
        assert_eq!(prefix, 1.0);
    }

    #[test]
    fn test_ws_token_expired_overshoot_is_unlabeled_and_bucketed() {
        let registry = Registry::new();
        let metrics = RelayMetrics::new_with_registry(&registry).unwrap();

        metrics.record_ws_token_expired_overshoot(3.0);
        metrics.record_ws_token_expired_overshoot(400.0);

        let families = registry.gather();
        let family = families
            .iter()
            .find(|f| f.get_name() == "relay_server_ws_token_expired_overshoot_seconds")
            .expect("histogram must be registered");
        assert_eq!(
            family.get_metric().len(),
            1,
            "exactly one series, no label fan-out"
        );
        let metric = &family.get_metric()[0];
        // Cardinality guard: adding a user/share/doc label here must turn this red.
        assert!(
            metric.get_label().is_empty(),
            "overshoot histogram must carry no labels, got {:?}",
            metric.get_label()
        );
        let h = metric.get_histogram();
        assert_eq!(h.get_sample_count(), 2);
        let cum = |le: f64| {
            h.get_bucket()
                .iter()
                .find(|b| b.get_upper_bound() == le)
                .map(|b| b.get_cumulative_count())
                .unwrap()
        };
        assert_eq!(cum(5.0), 1, "3s overshoot lands at <=5");
        assert_eq!(cum(300.0), 1, "400s overshoot is NOT <=300");
        assert_eq!(cum(3600.0), 2, "400s overshoot lands at <=3600");
    }

    #[test]
    fn test_http_auth_success_metrics() {
        let metrics = RelayMetrics::new_for_test().unwrap();

        metrics.record_http_auth_success("/doc/ws/:doc_id", "GET");
        metrics.record_http_auth_success("/doc/ws/:doc_id", "GET");

        let ws_success = metrics
            .http_auth_success_total
            .with_label_values(&["/doc/ws/:doc_id", "GET"])
            .get();
        assert_eq!(ws_success, 2.0);
    }

    #[test]
    fn test_sync_protocol_connections_gauge_is_observable_at_zero() {
        // A GaugeVec with no label dimensions has no series at all until the first
        // `set()` call — before the startup fix, the metric family was simply absent
        // from a scrape until the first connection opened, which is indistinguishable
        // from "no data collected" rather than "zero connections" (#f3cb5365 / #42323ab2).
        let metrics = RelayMetrics::new_for_test().unwrap();

        let value = metrics
            .sync_protocol_connections
            .with_label_values(&[])
            .get();
        assert_eq!(value, 0.0);
    }

    #[test]
    fn test_docs_active_gauge() {
        let metrics = RelayMetrics::new_for_test().unwrap();

        // Observable at zero without any prior set() call (startup-visibility fix,
        // same class as test_sync_protocol_connections_gauge_is_observable_at_zero).
        assert_eq!(metrics.docs_active.with_label_values(&[]).get(), 0.0);

        metrics.set_docs_active(3);
        assert_eq!(metrics.docs_active.with_label_values(&[]).get(), 3.0);

        metrics.set_docs_active(0);
        assert_eq!(metrics.docs_active.with_label_values(&[]).get(), 0.0);
    }

    #[test]
    fn test_doc_connection_open_close_counters() {
        let metrics = RelayMetrics::new_for_test().unwrap();

        metrics.record_doc_connection_opened();
        metrics.record_doc_connection_opened();
        assert_eq!(
            metrics.doc_connections_opened_total.with_label_values(&[]).get(),
            2.0
        );

        metrics.record_doc_connections_closed(2);
        assert_eq!(
            metrics.doc_connections_closed_total.with_label_values(&[]).get(),
            2.0
        );
    }

    #[test]
    fn test_clear_doc_subscription_removes_series_not_just_zeroes_it() {
        // Regression test for the cardinality leak this method fixes: a document
        // that drops to zero connections must stop appearing in the metric family
        // at all, not persist forever as a zero-value series (measured live on prod:
        // 71 such permanent series after 11h uptime under the old set(chan, 0) path).
        let metrics = RelayMetrics::new_for_test().unwrap();

        metrics.set_sync_protocol_subscriptions_by_channel("doc-a", 2);
        assert_eq!(
            metrics
                .sync_protocol_subscriptions_by_channel
                .with_label_values(&["doc-a"])
                .get(),
            2.0
        );

        metrics.clear_doc_subscription("doc-a");

        // Collect directly from the GaugeVec (it implements Collector itself, no
        // Registry needed) rather than via with_label_values/get, which would
        // silently recreate the series — that's the same lazy-child mechanism this
        // test needs to prove was actually torn down, not merely re-triggered.
        let series_exists = metrics
            .sync_protocol_subscriptions_by_channel
            .collect()
            .iter()
            .flat_map(|f| f.get_metric())
            .any(|m| m.get_label().iter().any(|l| l.get_value() == "doc-a"));
        assert!(
            !series_exists,
            "doc-a series should be fully removed, not left at 0"
        );

        // Calling it again on an already-absent series must not panic or error out
        // loudly — this is the double-clear path exercised by the real cleanup sweep.
        metrics.clear_doc_subscription("doc-a");
    }

    #[test]
    fn test_auth_error_metric_labels() {
        use crate::auth::AuthError;

        // Test that AuthError to_metric_label method works correctly
        assert_eq!(AuthError::InvalidToken.to_metric_label(), "invalid_format");
        assert_eq!(AuthError::Expired.to_metric_label(), "expired");
        assert_eq!(
            AuthError::InvalidSignature.to_metric_label(),
            "invalid_signature"
        );
        assert_eq!(AuthError::KeyMismatch.to_metric_label(), "key_mismatch");
        assert_eq!(
            AuthError::InvalidResource.to_metric_label(),
            "invalid_resource"
        );
    }
}
