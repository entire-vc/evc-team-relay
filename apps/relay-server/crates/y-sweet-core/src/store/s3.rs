use super::{FileInfo, FileListPage, LeasedValue, Result, StoreError, VersionInfo, WriteLease};
use crate::metrics::RelayMetrics;
use crate::store::Store;
use async_trait::async_trait;
use bytes::Bytes;
use jiff::Timestamp;
use reqwest::{Client, Method, Response, StatusCode, Url};
use rusty_s3::{Bucket, Credentials, S3Action};
use serde::{Deserialize, Serialize};
use std::env;
use std::sync::OnceLock;
use std::time::Duration;
use time::OffsetDateTime;
use url::Url as UrlParser;

const S3_ACCESS_KEY_ID: &str = "AWS_ACCESS_KEY_ID";
const S3_SECRET_ACCESS_KEY: &str = "AWS_SECRET_ACCESS_KEY";
const S3_SESSION_TOKEN: &str = "AWS_SESSION_TOKEN";
const S3_REGION: &str = "AWS_REGION";
const S3_ENDPOINT: &str = "AWS_ENDPOINT_URL_S3"; // Using consistent naming across tools
const S3_PUBLIC_ENDPOINT: &str = "S3_PUBLIC_ENDPOINT";
const S3_USE_PATH_STYLE: &str = "AWS_S3_USE_PATH_STYLE";
const DEFAULT_S3_REGION: &str = "us-east-1";

/// Read the text content of an element and decode it (handles both the
/// reader's character encoding and XML entity-escaping). `quick_xml` 0.41
/// changed `Reader::read_text` to return a `BytesText` instead of an
/// already-decoded `Cow<str>` (as it did in 0.31) — this restores the old
/// call-site ergonomics in one place.
fn read_decoded_text<'a>(
    reader: &mut quick_xml::Reader<&'a [u8]>,
    end: quick_xml::name::QName,
) -> quick_xml::Result<std::borrow::Cow<'a, str>> {
    reader.read_text(end)?.decode().map_err(Into::into)
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct S3Config {
    pub key: String,
    pub endpoint: String,
    pub secret: String,
    pub token: Option<String>,
    pub bucket: String,
    pub region: String,
    pub bucket_prefix: Option<String>,

    // Use old path-style URLs, needed to support some S3-compatible APIs (including some minio setups)
    pub path_style: bool,

    /// Endpoint embedded in presigned URLs handed to clients. Set when
    /// `endpoint` is only reachable inside the docker network (e.g.
    /// `http://minio:9000`). SigV4 signs the host, so the URL has to be
    /// signed against this host rather than rewritten afterwards. Server-side
    /// requests keep using `endpoint`.
    #[serde(default)]
    pub public_endpoint: Option<String>,
}

impl S3Config {
    /// Parse S3 configuration from environment variables
    ///
    /// This is the unified configuration parser used by both y-sweet and y-sign
    pub fn from_env(bucket: Option<String>, prefix: Option<String>) -> anyhow::Result<Self> {
        // First check for RELAY_SERVER_STORAGE which has highest precedence
        if let Ok(store_path) = env::var("RELAY_SERVER_STORAGE") {
            if store_path.starts_with("s3://") {
                // Parse the S3 URL to extract bucket and prefix
                let url = UrlParser::parse(&store_path)?;
                let bucket = url
                    .host_str()
                    .ok_or_else(|| anyhow::anyhow!("Invalid S3 URL"))?
                    .to_owned();

                let bucket_prefix = url.path().trim_start_matches('/').to_owned();
                let bucket_prefix = (!bucket_prefix.is_empty()).then_some(bucket_prefix); // "" => None

                return Self::build_config(bucket, bucket_prefix);
            }
        }

        // If bucket is provided as an argument, use it
        if let Some(bucket) = bucket {
            return Self::build_config(bucket, prefix);
        }

        // Otherwise, look for STORAGE_BUCKET or AWS_S3_BUCKET
        let bucket = env::var("STORAGE_BUCKET").or_else(|_| env::var("AWS_S3_BUCKET"))
            .map_err(|_| anyhow::anyhow!("Either RELAY_SERVER_STORAGE (s3:// URL) or STORAGE_BUCKET/AWS_S3_BUCKET environment variable is required"))?;

        // Use STORAGE_PREFIX for consistency with both tools
        let bucket_prefix = env::var("STORAGE_PREFIX")
            .or_else(|_| env::var("AWS_S3_BUCKET_PREFIX")) // Fall back to old name for compatibility
            .ok();

        Self::build_config(bucket, bucket_prefix)
    }

    fn build_config(bucket: String, bucket_prefix: Option<String>) -> anyhow::Result<Self> {
        let use_path_style = env::var(S3_USE_PATH_STYLE).ok();
        let path_style = if let Some(use_path_style) = use_path_style {
            if use_path_style.to_lowercase() == "true" || use_path_style == "1" {
                true
            } else if use_path_style.to_lowercase() == "false"
                || use_path_style.is_empty()
                || use_path_style == "0"
            {
                false
            } else {
                anyhow::bail!(
                    "If AWS_S3_USE_PATH_STYLE is set, it must be either \"true\", \"false\", \"1\", or \"0\""
                )
            }
        } else {
            false
        };

        let region = env::var(S3_REGION).unwrap_or_else(|_| DEFAULT_S3_REGION.to_string());
        let endpoint = env::var(S3_ENDPOINT)
            .unwrap_or_else(|_| format!("https://s3.dualstack.{}.amazonaws.com", &region));

        Ok(S3Config {
            key: env::var(S3_ACCESS_KEY_ID)
                .map_err(|_| anyhow::anyhow!("{} env var not supplied", S3_ACCESS_KEY_ID))?,
            secret: env::var(S3_SECRET_ACCESS_KEY)
                .map_err(|_| anyhow::anyhow!("{} env var not supplied", S3_SECRET_ACCESS_KEY))?,
            endpoint,
            region,
            token: env::var(S3_SESSION_TOKEN).ok(),
            bucket,
            bucket_prefix,
            path_style,
            public_endpoint: env::var(S3_PUBLIC_ENDPOINT).ok().filter(|v| !v.is_empty()),
        })
    }
}

const PRESIGNED_URL_DURATION: Duration = Duration::from_secs(60 * 60);

pub struct S3Store {
    pub bucket: Bucket,
    /// Bucket used only to sign URLs returned to clients; `None` means the
    /// internal `bucket` is also client-reachable.
    public_bucket: Option<Bucket>,
    _bucket_checked: OnceLock<()>,
    client: Client,
    pub credentials: Credentials,
    prefix: Option<String>,
}

#[derive(Debug)]
struct ListObjectsPage {
    files: Vec<FileInfo>,
    common_prefixes: Vec<String>,
    truncated: bool,
    next_continuation_token: Option<String>,
}

impl S3Store {
    pub fn new(config: S3Config) -> Self {
        let credentials = if let Some(token) = config.token {
            Credentials::new_with_token(config.key, config.secret, token)
        } else {
            Credentials::new(config.key, config.secret)
        };
        let endpoint: Url = config.endpoint.parse().expect("endpoint is a valid url");

        let path_style = if config.path_style {
            rusty_s3::UrlStyle::Path
        } else if endpoint.host_str() == Some("localhost") {
            // Since this was the old behavior before we added AWS_S3_USE_PATH_STYLE,
            // we continue to support it, but complain a bit.
            tracing::warn!("Inferring path-style URLs for localhost for backwards-compatibility. This behavior may change in the future. Set AWS_S3_USE_PATH_STYLE=true to ensure that path-style URLs are used.");
            rusty_s3::UrlStyle::Path
        } else {
            rusty_s3::UrlStyle::VirtualHost
        };

        let public_bucket = config.public_endpoint.as_deref().map(|public| {
            let public: Url = public.parse().expect("public endpoint is a valid url");
            Bucket::new(
                public,
                path_style,
                config.bucket.clone(),
                config.region.clone(),
            )
            .expect("Url has a valid scheme and host")
        });
        let bucket = Bucket::new(endpoint, path_style, config.bucket, config.region)
            .expect("Url has a valid scheme and host");
        let client = Client::new();

        S3Store {
            bucket,
            public_bucket,
            _bucket_checked: OnceLock::new(),
            client,
            credentials,
            prefix: config.bucket_prefix,
        }
    }

    fn public_bucket(&self) -> &Bucket {
        self.public_bucket.as_ref().unwrap_or(&self.bucket)
    }

    /// Generate a presigned URL for downloading a file from S3 with an optional existence check
    ///
    /// The key can be:
    /// - A simple file hash: "abcdef123456"
    /// - Already prefixed with files/: "files/abcdef123456"
    /// - A full path: "some/path/to/file"
    ///
    /// This method will add the files/ prefix if needed and handle storage prefixes.
    /// If check_exists is true, it will verify the file exists before generating a URL.
    pub async fn generate_download_url(
        &self,
        key: &str,
        check_exists: bool,
    ) -> Result<Option<String>> {
        self.init().await?;

        // Apply proper prefixing including files/ directory if needed
        let prefixed_key = self.prefixed_key(key);

        tracing::debug!(
            "Generating download URL for key: {} (prefixed as: {})",
            key,
            prefixed_key
        );

        // Check if object exists before generating URL if requested
        if check_exists && !self.exists(&key).await? {
            tracing::debug!("Object does not exist, not generating URL");
            return Ok(None);
        }

        let action = self
            .public_bucket()
            .get_object(Some(&self.credentials), &prefixed_key);
        let url = action.sign_with_time(PRESIGNED_URL_DURATION, &Timestamp::now());

        tracing::debug!("Generated download URL: {}", url);
        Ok(Some(url.to_string()))
    }

    async fn store_request<'a, A: S3Action<'a>>(
        &self,
        method: Method,
        action: A,
        body: Option<Vec<u8>>,
    ) -> Result<Response> {
        self.store_request_with_headers(method, action, body, &[])
            .await
    }

    async fn store_request_with_headers<'a, A: S3Action<'a>>(
        &self,
        method: Method,
        action: A,
        body: Option<Vec<u8>>,
        headers: &[(&str, &str)],
    ) -> Result<Response> {
        let url = action.sign_with_time(PRESIGNED_URL_DURATION, &Timestamp::now());
        let method_label = method.as_str().to_string();
        let mut request = self.client.request(method, url);

        for (name, value) in headers {
            request = request.header(*name, *value);
        }

        request = if let Some(body) = body {
            request.body(body.to_vec())
        } else {
            request
        };

        let response = request.send().await;

        let record = |outcome: &str| {
            if let Ok(metrics) = RelayMetrics::new() {
                metrics.record_s3_request(&method_label, outcome);
            }
        };

        let response = match response {
            Ok(response) => response,
            Err(e) => {
                record("connection_error");
                return Err(StoreError::ConnectionError(e.to_string()));
            }
        };

        match response.status() {
            StatusCode::OK => {
                record("ok");
                Ok(response)
            }
            StatusCode::NOT_FOUND => {
                record("not_found");
                Err(StoreError::DoesNotExist(
                    "Received NOT_FOUND from S3-compatible API.".to_string(),
                ))
            }
            StatusCode::FORBIDDEN => {
                record("forbidden");
                Err(StoreError::NotAuthorized(
                    "Received FORBIDDEN from S3-compatible API.".to_string(),
                ))
            }
            StatusCode::UNAUTHORIZED => {
                record("unauthorized");
                Err(StoreError::NotAuthorized(
                    "Received UNAUTHORIZED from S3-compatible API.".to_string(),
                ))
            }
            StatusCode::PRECONDITION_FAILED | StatusCode::CONFLICT => {
                record("lease_conflict");
                Err(StoreError::LeaseConflict(
                    "S3 object changed before conditional write".to_string(),
                ))
            }
            _ => {
                record("other_error");
                Err(StoreError::ConnectionError(format!(
                    "Received {} from S3-compatible API.",
                    response.status()
                )))
            }
        }
    }

    fn response_etag(response: &Response) -> Option<String> {
        response
            .headers()
            .get("etag")
            .and_then(|value| value.to_str().ok())
            .map(|value| value.to_string())
    }

    async fn read_response_bytes(response: Response) -> Result<Bytes> {
        match response.bytes().await {
            Ok(bytes) => Ok(bytes),
            Err(e) => Err(StoreError::ConnectionError(e.to_string())),
        }
    }

    /// Parse a `ListObjectsV2` XML response. Keys and common prefixes are
    /// returned with the bucket's configured `storage_prefix` stripped so that
    /// they match the keys callers use for `get`/`set`.
    fn parse_list_response(text: &str, storage_prefix: Option<&str>) -> Result<ListObjectsPage> {
        let mut reader = quick_xml::Reader::from_str(text);
        reader.config_mut().trim_text(true);

        let mut buf = Vec::new();
        let mut files = Vec::new();
        let mut common_prefixes = Vec::new();
        let mut truncated = false;
        let mut next_continuation_token = None;
        let mut in_contents = false;
        let mut in_common_prefixes = false;
        let mut current_key: Option<String> = None;
        let mut current_size: Option<u64> = None;
        let mut current_last_modified: Option<u64> = None;

        loop {
            match reader.read_event_into(&mut buf) {
                Ok(quick_xml::events::Event::Start(e)) => match e.name().as_ref() {
                    b"Contents" => in_contents = true,
                    b"CommonPrefixes" => in_common_prefixes = true,
                    b"Key" if in_contents => {
                        if let Ok(text) = read_decoded_text(&mut reader, e.name()) {
                            current_key = Some(text.to_string());
                        }
                    }
                    b"Prefix" if in_common_prefixes => {
                        if let Ok(text) = read_decoded_text(&mut reader, e.name()) {
                            let stripped =
                                Self::strip_storage_prefix(text.to_string(), storage_prefix);
                            if !stripped.is_empty() {
                                common_prefixes.push(stripped);
                            }
                        }
                    }
                    b"Size" if in_contents => {
                        if let Ok(text) = read_decoded_text(&mut reader, e.name()) {
                            current_size = text.parse::<u64>().ok();
                        }
                    }
                    b"LastModified" if in_contents => {
                        if let Ok(text) = read_decoded_text(&mut reader, e.name()) {
                            if let Ok(date_time) = time::OffsetDateTime::parse(
                                &text,
                                &time::format_description::well_known::Rfc3339,
                            ) {
                                current_last_modified =
                                    Some(date_time.unix_timestamp_nanos() as u64 / 1_000_000);
                            }
                        }
                    }
                    b"IsTruncated" => {
                        if let Ok(text) = read_decoded_text(&mut reader, e.name()) {
                            truncated = text.trim().eq_ignore_ascii_case("true");
                        }
                    }
                    b"NextContinuationToken" => {
                        if let Ok(text) = read_decoded_text(&mut reader, e.name()) {
                            next_continuation_token = Some(text.to_string());
                        }
                    }
                    _ => {}
                },
                Ok(quick_xml::events::Event::End(e)) => match e.name().as_ref() {
                    b"Contents" => {
                        in_contents = false;
                        if let (Some(key), Some(size), Some(last_modified)) = (
                            current_key.take(),
                            current_size.take(),
                            current_last_modified.take(),
                        ) {
                            let stripped = Self::strip_storage_prefix(key, storage_prefix);
                            if !stripped.is_empty() {
                                files.push(FileInfo {
                                    key: stripped,
                                    size,
                                    last_modified,
                                });
                            }
                        }
                    }
                    b"CommonPrefixes" => {
                        in_common_prefixes = false;
                    }
                    _ => {}
                },
                Ok(quick_xml::events::Event::Eof) => break,
                Err(e) => {
                    return Err(StoreError::ConnectionError(format!(
                        "Error parsing S3 list response: {}",
                        e
                    )));
                }
                _ => {}
            }
            buf.clear();
        }

        Ok(ListObjectsPage {
            files,
            common_prefixes,
            truncated,
            next_continuation_token,
        })
    }

    fn strip_storage_prefix(value: String, storage_prefix: Option<&str>) -> String {
        match storage_prefix {
            Some(prefix) => {
                let prefix = prefix.trim_end_matches('/');
                if prefix.is_empty() {
                    value
                } else if let Some(rest) = value.strip_prefix(&format!("{}/", prefix)) {
                    rest.to_string()
                } else {
                    // Value is outside the configured prefix (unexpected); pass
                    // through unchanged.
                    value
                }
            }
            None => value,
        }
    }

    async fn list_objects_page(
        &self,
        prefix: &str,
        continuation_token: Option<&str>,
        delimiter: Option<&str>,
    ) -> Result<FileListPage> {
        self.init().await?;

        // Apply storage prefix if configured
        let prefixed = if let Some(path_prefix) = &self.prefix {
            if path_prefix.ends_with('/') {
                format!("{}{}", path_prefix, prefix)
            } else {
                format!("{}/{}", path_prefix, prefix)
            }
        } else {
            prefix.to_string()
        };

        tracing::debug!("Listing objects with prefix: {}", prefixed);

        let mut action = self.bucket.list_objects_v2(Some(&self.credentials));
        action.with_prefix(prefixed.as_str());
        if let Some(delimiter) = delimiter {
            action.with_delimiter(delimiter);
        }
        if let Some(token) = continuation_token {
            action.with_continuation_token(token);
        }

        let response = self.store_request(Method::GET, action, None).await?;
        let bytes = Self::read_response_bytes(response).await?;
        let text = String::from_utf8_lossy(&bytes);
        let page = Self::parse_list_response(&text, self.prefix.as_deref())?;

        let next_continuation_token = if page.truncated {
            let next_token = page
                .next_continuation_token
                .filter(|token| !token.is_empty())
                .ok_or_else(|| {
                    StoreError::ConnectionError(format!(
                        "S3 list response for prefix {} was truncated but missing NextContinuationToken",
                        prefixed
                    ))
                })?;

            if continuation_token == Some(next_token.as_str()) {
                return Err(StoreError::ConnectionError(format!(
                    "S3 list response for prefix {} repeated continuation token {}",
                    prefixed, next_token
                )));
            }

            Some(next_token)
        } else {
            None
        };

        tracing::debug!(
            "Found {} files and {} common prefixes with prefix {} on current page",
            page.files.len(),
            page.common_prefixes.len(),
            prefixed
        );

        Ok(FileListPage {
            files: page.files,
            common_prefixes: page.common_prefixes,
            next_continuation_token,
        })
    }

    async fn list_objects(&self, prefix: &str) -> Result<Vec<FileInfo>> {
        let mut files = Vec::new();
        let mut continuation_token: Option<String> = None;
        let mut pages = 0usize;

        loop {
            let page = self
                .list_objects_page(prefix, continuation_token.as_deref(), None)
                .await?;
            pages += 1;
            files.extend(page.files);

            let Some(next_token) = page.next_continuation_token else {
                break;
            };
            continuation_token = Some(next_token);
        }

        tracing::debug!(
            "Found {} files with prefix {} across {} page(s)",
            files.len(),
            prefix,
            pages
        );
        Ok(files)
    }

    pub async fn init(&self) -> Result<()> {
        if self._bucket_checked.get().is_some() {
            return Ok(());
        }

        // Use ListObjectsV2 with max-keys=0 so that prefix-scoped IAM
        // policies (s3:ListBucket with s3:prefix condition) are sufficient.
        let mut action = self.bucket.list_objects_v2(Some(&self.credentials));
        action.with_max_keys(0);
        if let Some(prefix) = &self.prefix {
            action.with_prefix(prefix.as_str());
        }
        let result = self.store_request(Method::GET, action, None).await;

        match result {
            Err(StoreError::DoesNotExist(_)) => {
                return Err(StoreError::BucketDoesNotExist(
                    "Bucket does not exist or not accessible with the configured prefix."
                        .to_string(),
                ))
            }
            Err(e) => return Err(e),
            Ok(_) => {}
        };

        self._bucket_checked.set(()).unwrap();
        Ok(())
    }

    /// Create a key with the proper prefix and path structure
    ///
    /// This function adds the storage prefix if one is configured, and properly handles the files/ subdirectory
    /// for file storage. It also ensures proper handling of slashes to avoid duplicates.
    pub fn prefixed_key(&self, key: &str) -> String {
        // First, check if the key already starts with "files/" - we don't want to add it twice
        let key_with_files = if key.starts_with("files/") {
            key.to_string()
        } else if key.contains('/') {
            // Don't add "files/" if the key already contains a slash (likely a document path)
            key.to_string()
        } else {
            // For simple keys that don't have a path component, add the files/ directory
            format!("files/{}", key)
        };

        // Now add the storage prefix if one exists
        if let Some(path_prefix) = &self.prefix {
            // Handle trailing slashes in prefix to avoid double slashes
            if path_prefix.ends_with('/') {
                format!("{}{}", path_prefix, key_with_files)
            } else {
                format!("{}/{}", path_prefix, key_with_files)
            }
        } else {
            key_with_files
        }
    }

    async fn get(&self, key: &str) -> Result<Option<Vec<u8>>> {
        self.init().await?;
        let prefixed_key = self.prefixed_key(key);
        let object_get = self
            .bucket
            .get_object(Some(&self.credentials), &prefixed_key);
        let response = self.store_request(Method::GET, object_get, None).await;

        match response {
            Ok(response) => {
                let result = Self::read_response_bytes(response).await?;
                Ok(Some(result.to_vec()))
            }
            Err(StoreError::DoesNotExist(_)) => Ok(None),
            Err(e) => Err(e),
        }
    }

    async fn get_with_lease(&self, key: &str) -> Result<LeasedValue> {
        self.init().await?;
        let prefixed_key = self.prefixed_key(key);
        let object_get = self
            .bucket
            .get_object(Some(&self.credentials), &prefixed_key);
        let response = self.store_request(Method::GET, object_get, None).await;

        match response {
            Ok(response) => {
                let etag = Self::response_etag(&response);
                let result = Self::read_response_bytes(response).await?;
                let value = result.to_vec();
                let lease = etag
                    .map(|token| WriteLease::Opaque {
                        backend: "s3-etag".to_string(),
                        token,
                    })
                    .unwrap_or_else(|| WriteLease::for_value(Some(&value)));
                Ok(LeasedValue {
                    value: Some(value),
                    lease,
                })
            }
            Err(StoreError::DoesNotExist(_)) => Ok(LeasedValue::from_value(None)),
            Err(e) => Err(e),
        }
    }

    async fn get_version(&self, key: &str, version_id: &str) -> Result<Option<Vec<u8>>> {
        use rusty_s3::S3Action;
        self.init().await?;
        let prefixed_key = self.prefixed_key(key);
        let mut object_get = self
            .bucket
            .get_object(Some(&self.credentials), &prefixed_key);
        // S3 GetObject takes the version via the `versionId` query parameter.
        // rusty-s3 doesn't expose a builder method, but `query_mut()` lets
        // us insert it directly and the signing path picks it up.
        object_get.query_mut().insert("versionId", version_id);
        let response = self.store_request(Method::GET, object_get, None).await;

        match response {
            Ok(response) => {
                let result = Self::read_response_bytes(response).await?;
                Ok(Some(result.to_vec()))
            }
            Err(StoreError::DoesNotExist(_)) => Ok(None),
            Err(e) => Err(e),
        }
    }

    async fn set(&self, key: &str, value: Vec<u8>) -> Result<()> {
        self.init().await?;
        let prefixed_key = self.prefixed_key(key);
        let action = self
            .bucket
            .put_object(Some(&self.credentials), &prefixed_key);
        self.store_request(Method::PUT, action, Some(value)).await?;
        Ok(())
    }

    async fn set_if_unchanged(
        &self,
        key: &str,
        value: Vec<u8>,
        lease: &WriteLease,
    ) -> Result<WriteLease> {
        match lease {
            WriteLease::Opaque { backend, token } if backend == "s3-etag" => {
                self.init().await?;
                let prefixed_key = self.prefixed_key(key);
                let mut action = self
                    .bucket
                    .put_object(Some(&self.credentials), &prefixed_key);
                action.headers_mut().insert("if-match", token.clone());
                let headers = [("if-match", token.as_str())];
                let response = self
                    .store_request_with_headers(Method::PUT, action, Some(value.clone()), &headers)
                    .await?;
                Ok(Self::response_etag(&response)
                    .map(|token| WriteLease::Opaque {
                        backend: "s3-etag".to_string(),
                        token,
                    })
                    .unwrap_or_else(|| WriteLease::for_value(Some(&value))))
            }
            WriteLease::Missing => {
                self.init().await?;
                let prefixed_key = self.prefixed_key(key);
                let mut action = self
                    .bucket
                    .put_object(Some(&self.credentials), &prefixed_key);
                action.headers_mut().insert("if-none-match", "*");
                let headers = [("if-none-match", "*")];
                let response = self
                    .store_request_with_headers(Method::PUT, action, Some(value.clone()), &headers)
                    .await?;
                Ok(Self::response_etag(&response)
                    .map(|token| WriteLease::Opaque {
                        backend: "s3-etag".to_string(),
                        token,
                    })
                    .unwrap_or_else(|| WriteLease::for_value(Some(&value))))
            }
            _ => {
                let current = self.get_with_lease(key).await?;
                if !lease.matches(&current) {
                    return Err(StoreError::LeaseConflict(format!(
                        "{} changed since it was read",
                        key
                    )));
                }
                self.set(key, value.clone()).await?;
                Ok(WriteLease::for_value(Some(&value)))
            }
        }
    }

    async fn remove(&self, key: &str) -> Result<()> {
        self.init().await?;
        let prefixed_key = self.prefixed_key(key);
        let action = self
            .bucket
            .delete_object(Some(&self.credentials), &prefixed_key);
        self.store_request(Method::DELETE, action, None).await?;
        Ok(())
    }

    async fn exists(&self, key: &str) -> Result<bool> {
        self.init().await?;
        let prefixed_key = self.prefixed_key(key);
        let action = self
            .bucket
            .head_object(Some(&self.credentials), &prefixed_key);
        let response = self.store_request(Method::HEAD, action, None).await;
        match response {
            Ok(_) => Ok(true),
            Err(StoreError::DoesNotExist(_)) => Ok(false),
            Err(e) => Err(e),
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
#[async_trait]
impl Store for S3Store {
    async fn init(&self) -> Result<()> {
        self.init().await
    }

    async fn get(&self, key: &str) -> Result<Option<Vec<u8>>> {
        self.get(key).await
    }

    async fn get_with_lease(&self, key: &str) -> Result<LeasedValue> {
        self.get_with_lease(key).await
    }

    async fn get_version(&self, key: &str, version_id: &str) -> Result<Option<Vec<u8>>> {
        self.get_version(key, version_id).await
    }

    async fn set(&self, key: &str, value: Vec<u8>) -> Result<()> {
        self.set(key, value).await
    }

    async fn set_if_unchanged(
        &self,
        key: &str,
        value: Vec<u8>,
        lease: &WriteLease,
    ) -> Result<WriteLease> {
        self.set_if_unchanged(key, value, lease).await
    }

    async fn remove(&self, key: &str) -> Result<()> {
        self.remove(key).await
    }

    async fn exists(&self, key: &str) -> Result<bool> {
        self.exists(key).await
    }

    /// List files with a common prefix and return file info (key, size, last_modified)
    async fn list(&self, prefix: &str) -> Result<Vec<FileInfo>> {
        self.list_objects(prefix).await
    }

    async fn list_page(
        &self,
        prefix: &str,
        continuation_token: Option<&str>,
    ) -> Result<FileListPage> {
        self.list_objects_page(prefix, continuation_token, None)
            .await
    }

    async fn list_directory_page(
        &self,
        prefix: &str,
        continuation_token: Option<&str>,
    ) -> Result<FileListPage> {
        self.list_objects_page(prefix, continuation_token, Some("/"))
            .await
    }

    async fn list_versions(&self, key: &str) -> Result<Vec<VersionInfo>> {
        self.init().await?;

        let prefixed_key = if let Some(prefix) = &self.prefix {
            if prefix.ends_with('/') {
                format!("{}{}", prefix, key)
            } else {
                format!("{}/{}", prefix, key)
            }
        } else {
            key.to_string()
        };

        let mut action = self.bucket.list_object_versions(Some(&self.credentials));
        action.with_prefix(&prefixed_key);
        let url = action.sign_with_time(PRESIGNED_URL_DURATION, &Timestamp::now());

        let response = self
            .client
            .request(Method::GET, url)
            .send()
            .await
            .map_err(|e| StoreError::ConnectionError(e.to_string()))?;

        let status = response.status();
        let bytes = response
            .bytes()
            .await
            .map_err(|e| StoreError::ConnectionError(e.to_string()))?;

        if !status.is_success() {
            let body = String::from_utf8_lossy(&bytes);
            return Err(StoreError::ConnectionError(format!(
                "Failed to list object versions: HTTP {} — {}",
                status, body
            )));
        }

        // Parse the XML directly. We avoid `rusty_s3::actions::ListObjectVersions::parse_response`
        // because some S3 deployments return `<Owner>` blocks without a
        // `<DisplayName>` child, which that parser refuses; we don't need
        // owner info anyway. We extract only Key / VersionId / IsLatest /
        // LastModified from each `<Version>` block (and skip
        // `<DeleteMarker>` blocks).
        let text = String::from_utf8_lossy(&bytes);
        let mut reader = quick_xml::Reader::from_str(&text);
        reader.config_mut().trim_text(true);

        let mut buf = Vec::new();
        let mut versions: Vec<VersionInfo> = Vec::new();
        let mut in_version = false;
        let mut current_key: Option<String> = None;
        let mut current_version_id: Option<String> = None;
        let mut current_last_modified: Option<u64> = None;
        let mut current_is_latest = false;

        loop {
            match reader.read_event_into(&mut buf) {
                Ok(quick_xml::events::Event::Start(e)) => match e.name().as_ref() {
                    b"Version" => in_version = true,
                    b"Key" if in_version => {
                        if let Ok(t) = read_decoded_text(&mut reader, e.name()) {
                            current_key = Some(t.to_string());
                        }
                    }
                    b"VersionId" if in_version => {
                        if let Ok(t) = read_decoded_text(&mut reader, e.name()) {
                            current_version_id = Some(t.to_string());
                        }
                    }
                    b"IsLatest" if in_version => {
                        if let Ok(t) = read_decoded_text(&mut reader, e.name()) {
                            current_is_latest = t.eq_ignore_ascii_case("true");
                        }
                    }
                    b"LastModified" if in_version => {
                        if let Ok(t) = read_decoded_text(&mut reader, e.name()) {
                            if let Ok(dt) = OffsetDateTime::parse(
                                &t,
                                &time::format_description::well_known::Rfc3339,
                            ) {
                                current_last_modified =
                                    Some(dt.unix_timestamp_nanos() as u64 / 1_000_000);
                            }
                        }
                    }
                    _ => {}
                },
                Ok(quick_xml::events::Event::End(e)) => {
                    if e.name().as_ref() == b"Version" {
                        in_version = false;
                        if let (Some(key), Some(version_id), Some(last_modified)) = (
                            current_key.take(),
                            current_version_id.take(),
                            current_last_modified.take(),
                        ) {
                            if key == prefixed_key {
                                versions.push(VersionInfo {
                                    version_id,
                                    last_modified,
                                    is_latest: current_is_latest,
                                });
                            }
                        }
                        current_is_latest = false;
                    }
                }
                Ok(quick_xml::events::Event::Eof) => break,
                Err(e) => {
                    return Err(StoreError::ConnectionError(format!(
                        "Error parsing S3 list versions response: {}",
                        e
                    )));
                }
                _ => {}
            }
            buf.clear();
        }

        Ok(versions)
    }

    async fn generate_upload_url(
        &self,
        key: &str,
        content_type: Option<&str>,
        content_length: Option<u64>,
    ) -> Result<Option<String>> {
        self.init().await?;

        // Apply proper prefixing including files/ directory if needed
        let prefixed_key = self.prefixed_key(key);

        tracing::debug!(
            "Generating upload URL for key: {} (prefixed as: {})",
            key,
            prefixed_key
        );

        // Create action for presigned PUT request
        let mut action = self
            .public_bucket()
            .put_object(Some(&self.credentials), &prefixed_key);

        // Set content-type if provided
        if let Some(content_type) = content_type {
            action.headers_mut().insert("content-type", content_type);
            tracing::debug!("Added content-type: {}", content_type);
        }

        // Note: Content-Length is handled at validation time after upload,
        // as rusty-s3 doesn't support this constraint in the URL
        if let Some(length) = content_length {
            action
                .headers_mut()
                .insert("content-length".to_string(), length.to_string());
            tracing::debug!(
                "Added content-length constraint between 0 and {} bytes",
                length
            );
        }

        // Sign the URL with time
        let url = action.sign_with_time(PRESIGNED_URL_DURATION, &Timestamp::now());
        tracing::debug!("Generated upload URL: {}", url);

        Ok(Some(url.to_string()))
    }

    async fn generate_download_url(&self, key: &str) -> Result<Option<String>> {
        // Implement directly to avoid recursion with the S3Store method of the same name
        self.init().await?;

        // Apply proper prefixing including files/ directory if needed
        let prefixed_key = self.prefixed_key(key);

        tracing::debug!(
            "Generating download URL for key: {} (prefixed as: {})",
            key,
            prefixed_key
        );

        // Check if object exists before generating URL
        if !self.exists(&key).await? {
            tracing::debug!("Object does not exist, not generating URL");
            return Ok(None);
        }

        let action = self
            .public_bucket()
            .get_object(Some(&self.credentials), &prefixed_key);
        let url = action.sign_with_time(PRESIGNED_URL_DURATION, &Timestamp::now());

        tracing::debug!("Generated download URL: {}", url);
        Ok(Some(url.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    // Mock the S3Action trait to test the headers_mut functionality
    struct MockS3Action {
        headers: HashMap<String, String>,
    }

    impl MockS3Action {
        fn new() -> Self {
            Self {
                headers: HashMap::new(),
            }
        }

        fn headers_mut(&mut self) -> &mut HashMap<String, String> {
            &mut self.headers
        }
    }

    #[test]
    fn test_content_type_header_for_upload_url() {
        let mut action = MockS3Action::new();

        // Set a content type - using lowercase header name for S3 signature compatibility
        let content_type = "application/json";
        action
            .headers_mut()
            .insert("content-type".to_string(), content_type.to_string());

        // Verify the header was set
        assert_eq!(
            action.headers.get("content-type"),
            Some(&content_type.to_string())
        );
    }

    fn signed_host(public_endpoint: Option<&str>) -> (Option<String>, Option<String>) {
        use super::*;
        use rusty_s3::S3Action;
        use std::time::Duration;
        let store = S3Store::new(S3Config {
            key: "test-key".to_string(),
            endpoint: "http://minio:9000".to_string(),
            secret: "test-secret".to_string(),
            token: None,
            bucket: "test-bucket".to_string(),
            region: "us-east-1".to_string(),
            bucket_prefix: None,
            path_style: true,
            public_endpoint: public_endpoint.map(str::to_string),
        });
        let sign = |bucket: &rusty_s3::Bucket| {
            let url = bucket
                .get_object(Some(&store.credentials), "files/abc")
                .sign(Duration::from_secs(60));
            url.host_str().map(str::to_string)
        };
        (sign(store.public_bucket()), sign(&store.bucket))
    }

    #[test]
    fn test_presigned_url_uses_public_endpoint_when_set() {
        let (client_host, internal_host) = signed_host(Some("https://files.example.com"));
        assert_eq!(client_host.as_deref(), Some("files.example.com"));
        // Server-side requests keep the internal endpoint.
        assert_eq!(internal_host.as_deref(), Some("minio"));
    }

    #[test]
    fn test_presigned_url_uses_internal_endpoint_when_public_unset() {
        let (client_host, _) = signed_host(None);
        assert_eq!(client_host.as_deref(), Some("minio"));
    }

    #[test]
    fn test_prefixed_key_with_trailing_slash() {
        use super::*;

        let config = S3Config {
            key: "test-key".to_string(),
            endpoint: "http://localhost:9000".to_string(),
            secret: "test-secret".to_string(),
            token: None,
            bucket: "test-bucket".to_string(),
            region: "us-east-1".to_string(),
            bucket_prefix: Some("prefix/".to_string()),
            path_style: true,
            public_endpoint: None,
        };

        let store = S3Store::new(config);

        // Test with file hash
        let result = store.prefixed_key("testkey");
        assert_eq!(result, "prefix/files/testkey");

        // Test with explicit files/ prefix
        let result = store.prefixed_key("files/testkey");
        assert_eq!(result, "prefix/files/testkey");

        // Test with deep path
        let result = store.prefixed_key("docs/testkey");
        assert_eq!(result, "prefix/docs/testkey");
    }

    #[test]
    fn test_prefixed_key_without_trailing_slash() {
        use super::*;

        let config = S3Config {
            key: "test-key".to_string(),
            endpoint: "http://localhost:9000".to_string(),
            secret: "test-secret".to_string(),
            token: None,
            bucket: "test-bucket".to_string(),
            region: "us-east-1".to_string(),
            bucket_prefix: Some("prefix".to_string()),
            path_style: true,
            public_endpoint: None,
        };

        let store = S3Store::new(config);

        // Test with file hash
        let result = store.prefixed_key("testkey");
        assert_eq!(result, "prefix/files/testkey");

        // Test with explicit files/ prefix
        let result = store.prefixed_key("files/testkey");
        assert_eq!(result, "prefix/files/testkey");

        // Test with deep path
        let result = store.prefixed_key("docs/testkey");
        assert_eq!(result, "prefix/docs/testkey");
    }

    #[test]
    fn test_prefixed_key_no_prefix() {
        use super::*;

        let config = S3Config {
            key: "test-key".to_string(),
            endpoint: "http://localhost:9000".to_string(),
            secret: "test-secret".to_string(),
            token: None,
            bucket: "test-bucket".to_string(),
            region: "us-east-1".to_string(),
            bucket_prefix: None,
            path_style: true,
            public_endpoint: None,
        };

        let store = S3Store::new(config);

        // Test with file hash - should add files/
        let result = store.prefixed_key("testkey");
        assert_eq!(result, "files/testkey");

        // Test with explicit files/ prefix - should not duplicate
        let result = store.prefixed_key("files/testkey");
        assert_eq!(result, "files/testkey");

        // Test with other path - should not add files/
        let result = store.prefixed_key("docs/testkey");
        assert_eq!(result, "docs/testkey");
    }

    #[test]
    fn test_parse_list_objects_xml() {
        use super::*;

        // Sample S3 ListObjectsV2 response XML
        let xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
    <Name>test-bucket</Name>
    <Prefix>files/test-doc/</Prefix>
    <KeyCount>3</KeyCount>
    <MaxKeys>1000</MaxKeys>
    <IsTruncated>false</IsTruncated>
    <Contents>
        <Key>files/test-doc/abc123</Key>
        <LastModified>2023-01-15T10:00:00.000Z</LastModified>
        <ETag>"d41d8cd98f00b204e9800998ecf8427e"</ETag>
        <Size>1024</Size>
        <StorageClass>STANDARD</StorageClass>
    </Contents>
    <Contents>
        <Key>files/test-doc/def456</Key>
        <LastModified>2023-01-16T11:30:00.000Z</LastModified>
        <ETag>"d41d8cd98f00b204e9800998ecf8427e"</ETag>
        <Size>2048</Size>
        <StorageClass>STANDARD</StorageClass>
    </Contents>
    <Contents>
        <Key>files/test-doc/ghi789</Key>
        <LastModified>2023-01-17T12:45:00.000Z</LastModified>
        <ETag>"d41d8cd98f00b204e9800998ecf8427e"</ETag>
        <Size>3072</Size>
        <StorageClass>STANDARD</StorageClass>
    </Contents>
</ListBucketResult>"#;

        let page = S3Store::parse_list_response(xml, None).unwrap();

        // Verify the results
        assert_eq!(page.files.len(), 3);
        assert_eq!(page.common_prefixes, Vec::<String>::new());
        assert!(!page.truncated);
        assert_eq!(page.next_continuation_token, None);

        // Check first file
        assert_eq!(page.files[0].key, "files/test-doc/abc123");
        assert_eq!(page.files[0].size, 1024);

        // Check second file
        assert_eq!(page.files[1].key, "files/test-doc/def456");
        assert_eq!(page.files[1].size, 2048);

        // Check third file
        assert_eq!(page.files[2].key, "files/test-doc/ghi789");
        assert_eq!(page.files[2].size, 3072);
    }

    #[test]
    fn test_parse_list_objects_xml_strips_storage_prefix_and_reads_token() {
        use super::*;

        let xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
    <Name>test-bucket</Name>
    <Prefix>tenant/files/test-doc/</Prefix>
    <KeyCount>1</KeyCount>
    <MaxKeys>1000</MaxKeys>
    <IsTruncated>true</IsTruncated>
    <NextContinuationToken>page-2</NextContinuationToken>
    <CommonPrefixes>
        <Prefix>tenant/files/test-doc/nested/</Prefix>
    </CommonPrefixes>
    <Contents>
        <Key>tenant/files/test-doc/abc123</Key>
        <LastModified>2023-01-15T10:00:00.000Z</LastModified>
        <ETag>"d41d8cd98f00b204e9800998ecf8427e"</ETag>
        <Size>1024</Size>
        <StorageClass>STANDARD</StorageClass>
    </Contents>
</ListBucketResult>"#;

        let page = S3Store::parse_list_response(xml, Some("tenant/")).unwrap();

        assert!(page.truncated);
        assert_eq!(page.next_continuation_token.as_deref(), Some("page-2"));
        assert_eq!(page.common_prefixes, vec!["files/test-doc/nested/"]);
        assert_eq!(page.files.len(), 1);
        assert_eq!(page.files[0].key, "files/test-doc/abc123");
        assert_eq!(page.files[0].size, 1024);
    }
}

#[cfg(target_arch = "wasm32")]
#[async_trait(?Send)]
impl Store for S3Store {
    async fn init(&self) -> Result<()> {
        self.init().await
    }

    async fn get(&self, key: &str) -> Result<Option<Vec<u8>>> {
        self.get(key).await
    }

    async fn get_version(&self, key: &str, version_id: &str) -> Result<Option<Vec<u8>>> {
        self.get_version(key, version_id).await
    }

    async fn set(&self, key: &str, value: Vec<u8>) -> Result<()> {
        self.set(key, value).await
    }

    async fn remove(&self, key: &str) -> Result<()> {
        self.remove(key).await
    }

    async fn exists(&self, key: &str) -> Result<bool> {
        self.exists(key).await
    }

    /// List files with a common prefix and return file info (key, size, last_modified)
    async fn list(&self, prefix: &str) -> Result<Vec<FileInfo>> {
        self.list_objects(prefix).await
    }

    async fn list_page(
        &self,
        prefix: &str,
        continuation_token: Option<&str>,
    ) -> Result<FileListPage> {
        self.list_objects_page(prefix, continuation_token, None)
            .await
    }

    async fn list_directory_page(
        &self,
        prefix: &str,
        continuation_token: Option<&str>,
    ) -> Result<FileListPage> {
        self.list_objects_page(prefix, continuation_token, Some("/"))
            .await
    }

    async fn generate_upload_url(
        &self,
        key: &str,
        content_type: Option<&str>,
        content_length: Option<u64>,
    ) -> Result<Option<String>> {
        self.init().await?;

        // Apply proper prefixing including files/ directory if needed
        let prefixed_key = self.prefixed_key(key);

        tracing::debug!(
            "Generating upload URL for key: {} (prefixed as: {})",
            key,
            prefixed_key
        );

        // Create action for presigned PUT request
        let mut action = self
            .public_bucket()
            .put_object(Some(&self.credentials), &prefixed_key);

        // Set content-type if provided
        if let Some(content_type) = content_type {
            action.headers_mut().insert("content-type", content_type);
            tracing::debug!("Added content-type: {}", content_type);
        }

        // Note: Content-Length is handled at validation time after upload,
        // as rusty-s3 doesn't support this constraint in the URL
        if let Some(length) = content_length {
            tracing::debug!(
                "Content length constraint of {} bytes will be validated after upload",
                length
            );
        }

        // Sign the URL with time
        let url = action.sign_with_time(PRESIGNED_URL_DURATION, &Timestamp::now());
        tracing::debug!("Generated upload URL: {}", url);

        Ok(Some(url.to_string()))
    }

    async fn generate_download_url(&self, key: &str) -> Result<Option<String>> {
        // Implement directly to avoid recursion with the S3Store method of the same name
        self.init().await?;

        // Apply proper prefixing including files/ directory if needed
        let prefixed_key = self.prefixed_key(key);

        tracing::debug!(
            "Generating download URL for key: {} (prefixed as: {})",
            key,
            prefixed_key
        );

        // Check if object exists before generating URL
        if !self.exists(&key).await? {
            tracing::debug!("Object does not exist, not generating URL");
            return Ok(None);
        }

        let action = self
            .public_bucket()
            .get_object(Some(&self.credentials), &prefixed_key);
        let url = action.sign_with_time(PRESIGNED_URL_DURATION, &Timestamp::now());

        tracing::debug!("Generated download URL: {}", url);
        Ok(Some(url.to_string()))
    }
}
