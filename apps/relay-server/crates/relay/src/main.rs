use anyhow::Context;
use anyhow::Result;
use axum::middleware;
use clap::{Args, Parser, Subcommand, ValueEnum};
use relay::cli::{print_auth_message, sign_stdin, verify_stdin};
use relay::server::AllowedHost;
use relay::stores::filesystem::FileSystemStore;
use serde_json::json;
use std::{
    env,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    path::PathBuf,
    sync::Arc,
};
use tokio::io::AsyncReadExt;
use tokio::net::TcpListener;
use tokio_util::sync::CancellationToken;
use tracing::metadata::LevelFilter;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};
use url::Url;
use y_sweet_core::{
    auth::Authenticator,
    config::Config,
    store::{
        s3::{S3Config, S3Store},
        Store,
    },
};

const VERSION: &str = env!("CARGO_PKG_VERSION");

fn generate_public_key_from_private(private_key_b64: &str) -> Result<String, anyhow::Error> {
    use p256::SecretKey;
    use y_sweet_core::auth::BASE64_CUSTOM;

    let private_key_bytes = BASE64_CUSTOM.decode(private_key_b64.as_bytes())?;
    let secret_key = SecretKey::from_slice(&private_key_bytes)?;
    let public_key = secret_key.public_key();
    let public_key_bytes = public_key.to_sec1_bytes();

    Ok(BASE64_CUSTOM.encode(&public_key_bytes))
}

fn generate_ed25519_public_key_from_private(
    private_key_b64: &str,
) -> Result<String, anyhow::Error> {
    use ed25519_dalek::{SecretKey as Ed25519SecretKey, SigningKey};
    use y_sweet_core::auth::BASE64_CUSTOM;

    let private_key_bytes = BASE64_CUSTOM.decode(private_key_b64.as_bytes())?;
    let secret_key: Ed25519SecretKey = private_key_bytes.as_slice().try_into()?;
    let signing_key = SigningKey::from(&secret_key);
    let public_key_bytes = signing_key.verifying_key().to_bytes();

    Ok(BASE64_CUSTOM.encode(&public_key_bytes))
}

#[derive(Clone, ValueEnum)]
enum KeyType {
    #[value(name = "legacy")]
    Legacy,
    #[value(name = "HMAC256")]
    Hmac256,
    #[value(name = "ES256")]
    Es256,
    #[value(name = "EdDSA")]
    EdDsa,
}

#[derive(Parser)]
struct Opts {
    #[clap(subcommand)]
    subcmd: ServSubcommand,
}

#[derive(Subcommand)]
enum ServSubcommand {
    Serve {
        /// Path to configuration file
        #[clap(short = 'c', long = "config")]
        config: Option<PathBuf>,

        // Legacy CLI arguments - kept for backward compatibility
        #[clap()]
        store: Option<String>,

        #[clap(long)]
        port: Option<u16>,
        #[clap(long)]
        host: Option<IpAddr>,
        #[clap(long)]
        metrics_port: Option<u16>,
        #[clap(long)]
        checkpoint_freq_seconds: Option<u64>,

        #[clap(long)]
        auth: Option<String>,

        #[clap(long)]
        url: Option<String>,

        #[clap(long, value_delimiter = ',')]
        allowed_hosts: Option<Vec<String>>,

        /// Run pending data migrations before binding the listener. If any
        /// migration fails, the server will not start. Useful for baking
        /// migrations into a single-tenant container image's entrypoint.
        #[clap(long)]
        migrate: bool,
    },

    /// Run any pending data migrations against the configured store, then exit.
    Migrate {
        /// Path to relay.toml (defaults to standard discovery).
        #[clap(short = 'c', long = "config")]
        config: Option<PathBuf>,

        /// Store URL override: `s3://bucket[/prefix]` or a filesystem path.
        /// If omitted, the configured store from relay.toml/env is used.
        #[clap(long)]
        store: Option<String>,
    },

    GenAuth {
        #[clap(long)]
        json: bool,

        #[clap(long, default_value = "legacy")]
        key_type: KeyType,
    },

    /// Convert from a YDoc v1 update format to a .ysweet file.
    /// The YDoc update should be passed in via stdin.
    ConvertFromUpdate {
        /// The store to write the document to.
        #[clap()]
        store: String,

        /// The ID of the document to write.
        doc_id: String,
    },

    Version,

    /// Configuration management commands
    Config {
        #[clap(subcommand)]
        cmd: ConfigSubcommand,
    },

    Sign {
        #[clap(long)]
        auth: String,
    },

    Verify {
        #[clap(long)]
        auth: String,

        #[clap(long)]
        doc_id: Option<String>,

        #[clap(long)]
        file_hash: Option<String>,
    },

    /// Subdoc-related maintenance commands.
    Subdocs {
        #[clap(subcommand)]
        cmd: SubdocsCommand,
    },

    /// Per-document maintenance and inspection commands.
    Doc {
        #[clap(subcommand)]
        cmd: DocCommand,
    },
}

#[derive(Subcommand)]
enum DocCommand {
    /// Inspect a single doc — print envelope metadata, KV layout,
    /// per-user contributions, or update history.
    Inspect {
        #[clap(subcommand)]
        cmd: DocInspectCommand,
    },

    /// List S3 object versions for a single doc (newest first).
    /// Requires the bucket to have versioning enabled.
    Versions {
        /// Path to relay.toml (defaults to standard discovery).
        #[clap(short = 'c', long = "config")]
        config: Option<PathBuf>,

        /// Store URL override: `s3://bucket[/prefix]` or a filesystem path.
        #[clap(long)]
        store: Option<String>,

        /// Relay GUID prefix used to construct the storage key.
        #[clap(long = "relay")]
        relay_id: String,

        /// Doc GUID to list versions for.
        #[clap(long)]
        doc: String,

        /// Limit the number of versions shown (0 = no limit).
        #[clap(long, default_value_t = 25)]
        limit: usize,
    },

    /// Fetch the raw `data.ysweet` bytes for a doc and write them to a file
    /// (or stdout). With `--version`, fetches a specific past version.
    Get {
        /// Path to relay.toml (defaults to standard discovery).
        #[clap(short = 'c', long = "config")]
        config: Option<PathBuf>,

        /// Store URL override: `s3://bucket[/prefix]` or a filesystem path.
        #[clap(long)]
        store: Option<String>,

        /// Relay GUID prefix used to construct the storage key.
        #[clap(long = "relay")]
        relay_id: String,

        /// Doc GUID.
        #[clap(long)]
        doc: String,

        /// Specific S3 VersionId to fetch. If omitted, fetches the latest.
        #[clap(long)]
        version: Option<String>,

        /// Output path. Defaults to stdout.
        #[clap(short = 'o', long)]
        output: Option<PathBuf>,
    },

    /// Restore supported document roots from a historical version by writing
    /// fresh Yjs operations into the current doc.
    Restore {
        #[clap(flatten)]
        args: DocRestoreArgs,
    },
}

#[derive(Args)]
struct DocRestoreArgs {
    /// Path to relay.toml (defaults to standard discovery).
    #[clap(short = 'c', long = "config")]
    config: Option<PathBuf>,

    /// Store URL override: `s3://bucket[/prefix]` or a filesystem path.
    #[clap(long)]
    store: Option<String>,

    /// Relay GUID prefix used to construct the storage key.
    #[clap(long = "relay")]
    relay_id: String,

    /// Doc GUID to restore.
    #[clap(long)]
    doc: String,

    /// Specific S3 VersionId to restore supported roots from.
    #[clap(long = "from-version")]
    from_version: String,

    /// Restore only this top-level root. Repeat for multiple roots.
    #[clap(long, conflicts_with = "except")]
    only: Vec<String>,

    /// Exclude this top-level root. Repeat for multiple roots.
    #[clap(long)]
    except: Vec<String>,

    /// Persist the restored roots. Without this flag, this is a dry run.
    #[clap(long, conflicts_with = "verify")]
    write: bool,

    /// Verify current root entries and writer client clocks against source.
    #[clap(long, conflicts_with = "write")]
    verify: bool,
}

#[derive(Subcommand)]
enum DocInspectCommand {
    /// Text dump of file format, metadata, KV layout, and yrs doc stats.
    Info {
        #[clap(flatten)]
        input: InspectInput,

        /// Also print the raw key-value entries.
        #[clap(long)]
        keys: bool,
    },

    /// Per-user content contributions as JSON.
    Users {
        #[clap(flatten)]
        input: InspectInput,
    },

    /// Timeline of update content diffs as JSON.
    History {
        #[clap(flatten)]
        input: InspectInput,
    },
}

/// Where the bytes for `relay doc inspect` come from. Either a local file,
/// or fetched from the configured store using `{relay}-{doc}/data.ysweet`.
#[derive(Args)]
struct InspectInput {
    /// Read from a local .ysweet file. Mutually exclusive with --doc.
    #[clap(long)]
    file: Option<PathBuf>,

    /// Doc GUID to fetch from the configured store. Requires --relay.
    #[clap(long, requires = "relay_id", conflicts_with = "file")]
    doc: Option<String>,

    /// Relay GUID prefix used to construct the storage key.
    #[clap(long = "relay", requires = "doc")]
    relay_id: Option<String>,

    /// Store URL override: `s3://bucket[/prefix]` or a filesystem path.
    /// If omitted, the configured store from relay.toml/env is used.
    #[clap(long)]
    store: Option<String>,

    /// Path to relay.toml (defaults to standard discovery).
    #[clap(short = 'c', long = "config")]
    config: Option<PathBuf>,
}

#[derive(Subcommand)]
enum SubdocsCommand {
    /// Rebuild the parent folder's `metadata.subdocs` snapshot index by
    /// walking `filemeta_v0`, fetching each child doc, encoding its
    /// snapshot, and writing the result back to the parent.
    ///
    /// The store is taken from the relay config (relay.toml + env vars,
    /// same resolution as `serve`) unless `--store` is given as an override.
    Index {
        /// Path to relay.toml (defaults to standard discovery).
        #[clap(short = 'c', long = "config")]
        config: Option<PathBuf>,

        /// Store URL override: `s3://bucket[/prefix]` or a filesystem path.
        /// If omitted, the configured store from relay.toml/env is used.
        #[clap(long)]
        store: Option<String>,

        /// Relay GUID prefix used to construct storage keys
        /// (`{relay}-{doc}/data.ysweet`).
        #[clap(long = "relay")]
        relay_id: String,

        /// Folder doc GUID.
        #[clap(long)]
        folder: String,

        /// Print the current coverage of the parent's subdocs index against
        /// its `filemeta_v0` and exit. No fetches, no writes.
        #[clap(long)]
        check: bool,

        /// Compute everything but skip both the in-memory parent mutation
        /// and the persist back to storage. Ignored if `--check` is set.
        #[clap(long)]
        dry_run: bool,
    },
}

#[derive(Subcommand)]
enum ConfigSubcommand {
    /// Validate a TOML configuration file
    Validate {
        /// Path to configuration file to validate
        #[clap(short = 'c', long = "config", default_value = "relay.toml")]
        config: PathBuf,
    },

    /// Show the current configuration (merged from file and environment)
    Show {
        /// Path to configuration file
        #[clap(short = 'c', long = "config", default_value = "relay.toml")]
        config: PathBuf,
    },
}

fn load_config_for_serve_args(
    config: Option<&PathBuf>,
    // CLI overrides
    store: &Option<String>,
    port: &Option<u16>,
    host: &Option<IpAddr>,
    metrics_port: &Option<u16>,
    checkpoint_freq_seconds: &Option<u64>,
    auth: &Option<String>,
    url: &Option<String>,
    allowed_hosts: &Option<Vec<String>>,
) -> Result<Config> {
    // Load base configuration
    let mut config = Config::load(config.as_deref().map(|v| v.as_path()))?;

    // Apply CLI overrides (these have highest precedence)
    if let Some(store_path) = store {
        use y_sweet_core::config::{FilesystemStoreConfig, S3StoreConfig, StoreConfig};

        if store_path.starts_with("s3://") {
            let url = Url::parse(store_path)?;
            let bucket = url
                .host_str()
                .ok_or_else(|| anyhow::anyhow!("Invalid S3 URL: missing bucket"))?
                .to_string();
            let prefix = url.path().trim_start_matches('/');
            let prefix = if prefix.is_empty() {
                String::new()
            } else {
                prefix.to_string()
            };

            config.store = StoreConfig::S3(S3StoreConfig {
                bucket,
                prefix,
                region: env::var("AWS_REGION").unwrap_or_else(|_| "us-east-1".to_string()),
                endpoint: String::new(),
                path_style: false,
                presigned_url_expiration: 3600,
                access_key_id: None,
                secret_access_key: None,
            });
        } else {
            config.store = StoreConfig::Filesystem(FilesystemStoreConfig {
                path: store_path.clone(),
            });
        }
    }

    // Override server settings with CLI args (always apply when provided)
    if let Some(port_val) = port {
        config.server.port = *port_val;
    }
    if let Some(port) = metrics_port {
        if config.metrics.is_none() {
            config.metrics = Some(y_sweet_core::config::MetricsConfig { port: *port });
        } else if let Some(ref mut metrics_config) = config.metrics {
            metrics_config.port = *port;
        }
    }
    if let Some(freq) = checkpoint_freq_seconds {
        config.server.checkpoint_freq_seconds = *freq;
    }

    if let Some(host) = host {
        config.server.host = host.to_string();
    }

    if let Some(auth_key) = auth {
        if config.auth.is_empty() {
            config.auth.push(y_sweet_core::config::AuthKeyConfig {
                key_id: None,
                private_key: Some(auth_key.clone()),
                public_key: None,
                allowed_token_types: vec![
                    y_sweet_core::config::TokenType::Document,
                    y_sweet_core::config::TokenType::File,
                    y_sweet_core::config::TokenType::Server,
                    y_sweet_core::config::TokenType::Prefix,
                ],
            });
        } else {
            // Update the first auth entry
            config.auth[0].private_key = Some(auth_key.clone());
        }
    }

    if let Some(url) = url {
        config.server.url = Some(url.clone());
    }

    if let Some(allowed_hosts) = allowed_hosts {
        let parsed_hosts = parse_allowed_hosts(allowed_hosts.clone())?;
        config.server.allowed_hosts = parsed_hosts
            .into_iter()
            .map(|h| y_sweet_core::config::AllowedHost {
                host: h.host,
                scheme: h.scheme,
            })
            .collect();
    }

    Ok(config)
}

fn get_store_from_config(
    store_config: &y_sweet_core::config::StoreConfig,
) -> Result<Option<Box<dyn Store>>> {
    use y_sweet_core::config::StoreConfig;

    match store_config {
        StoreConfig::Memory => Ok(None),
        StoreConfig::Filesystem(fs_config) => {
            let store = FileSystemStore::new(PathBuf::from(&fs_config.path))?;
            Ok(Some(Box::new(store)))
        }
        StoreConfig::S3(s3_config) => {
            // Build S3 configuration from our config
            let s3_store_config = S3Config {
                key: s3_config
                    .access_key_id
                    .clone()
                    .or_else(|| env::var("AWS_ACCESS_KEY_ID").ok())
                    .ok_or_else(|| anyhow::anyhow!("AWS_ACCESS_KEY_ID is required"))?,
                secret: s3_config
                    .secret_access_key
                    .clone()
                    .or_else(|| env::var("AWS_SECRET_ACCESS_KEY").ok())
                    .ok_or_else(|| anyhow::anyhow!("AWS_SECRET_ACCESS_KEY is required"))?,
                token: env::var("AWS_SESSION_TOKEN").ok(),
                endpoint: if !s3_config.endpoint.is_empty() {
                    s3_config.endpoint.clone()
                } else if let Ok(ep) = env::var("AWS_ENDPOINT_URL_S3") {
                    ep
                } else {
                    format!("https://s3.dualstack.{}.amazonaws.com", s3_config.region)
                },
                region: s3_config.region.clone(),
                bucket: s3_config.bucket.clone(),
                bucket_prefix: if s3_config.prefix.is_empty() {
                    None
                } else {
                    Some(s3_config.prefix.clone())
                },
                path_style: s3_config.path_style,
                public_endpoint: env::var("S3_PUBLIC_ENDPOINT").ok().filter(|v| !v.is_empty()),
            };

            let store = S3Store::new(s3_store_config);
            Ok(Some(Box::new(store)))
        }
        // Convert provider-specific configs to generic S3 config
        StoreConfig::Aws(_)
        | StoreConfig::Cloudflare(_)
        | StoreConfig::Backblaze(_)
        | StoreConfig::Minio(_)
        | StoreConfig::Tigris(_) => {
            let s3_config = store_config
                .to_s3_config()
                .ok_or_else(|| anyhow::anyhow!("Failed to convert provider config to S3 config"))?;

            // Build S3 configuration from our config
            let s3_store_config = S3Config {
                key: s3_config
                    .access_key_id
                    .clone()
                    .or_else(|| env::var("AWS_ACCESS_KEY_ID").ok())
                    .ok_or_else(|| anyhow::anyhow!("AWS_ACCESS_KEY_ID is required"))?,
                secret: s3_config
                    .secret_access_key
                    .clone()
                    .or_else(|| env::var("AWS_SECRET_ACCESS_KEY").ok())
                    .ok_or_else(|| anyhow::anyhow!("AWS_SECRET_ACCESS_KEY is required"))?,
                token: env::var("AWS_SESSION_TOKEN").ok(),
                endpoint: if !s3_config.endpoint.is_empty() {
                    s3_config.endpoint.clone()
                } else if let Ok(ep) = env::var("AWS_ENDPOINT_URL_S3") {
                    ep
                } else {
                    format!("https://s3.dualstack.{}.amazonaws.com", s3_config.region)
                },
                region: s3_config.region.clone(),
                bucket: s3_config.bucket.clone(),
                bucket_prefix: if s3_config.prefix.is_empty() {
                    None
                } else {
                    Some(s3_config.prefix.clone())
                },
                path_style: s3_config.path_style,
                public_endpoint: env::var("S3_PUBLIC_ENDPOINT").ok().filter(|v| !v.is_empty()),
            };

            let store = S3Store::new(s3_store_config);
            Ok(Some(Box::new(store)))
        }
    }
}

/// Resolve a store for an offline subcommand: explicit `--store` override
/// wins; otherwise load relay.toml (default discovery if `config` is None)
/// and use its configured store.
fn build_store_for_subcommand(
    store_override: Option<&str>,
    config_path: Option<&PathBuf>,
) -> Result<Arc<Box<dyn Store>>> {
    let raw: Box<dyn Store> = if let Some(arg) = store_override {
        get_store_from_opts(arg)?
    } else {
        let cfg = Config::load(config_path.map(|p| p.as_path()))?;
        get_store_from_config(&cfg.store)?.ok_or_else(|| {
            anyhow::anyhow!(
                "configured store is in-memory; this subcommand requires a real store. \
                 Pass --store s3://... or --config <path> pointing at a config with a store."
            )
        })?
    };
    Ok(Arc::new(raw))
}

/// Resolve an `InspectInput` to (label, bytes). Either reads a local file
/// or fetches `{relay_id}-{doc}/data.ysweet` from the configured store.
async fn resolve_inspect_input(input: &InspectInput) -> Result<(String, Vec<u8>)> {
    if let Some(path) = &input.file {
        let bytes =
            std::fs::read(path).with_context(|| format!("failed to read {}", path.display()))?;
        Ok((path.display().to_string(), bytes))
    } else {
        let doc = input
            .doc
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("must pass --file or --doc"))?;
        let relay_id = input
            .relay_id
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("--doc requires --relay"))?;
        let store = build_store_for_subcommand(input.store.as_deref(), input.config.as_ref())?;
        store.init().await.context("store init failed")?;
        let data_key = format!("{}-{}/data.ysweet", relay_id, doc);
        let bytes = store
            .get(&data_key)
            .await
            .with_context(|| format!("get failed for {}", data_key))?
            .ok_or_else(|| anyhow::anyhow!("no object at {}", data_key))?;
        Ok((data_key, bytes))
    }
}

fn get_store_from_opts(store_path: &str) -> Result<Box<dyn Store>> {
    if store_path.starts_with("s3://") {
        // Set the RELAY_SERVER_STORAGE environment variable so S3Config::from_env can use it
        env::set_var("RELAY_SERVER_STORAGE", store_path);

        // Use the unified S3Config::from_env method
        let config = S3Config::from_env(None, None)?;
        let store = S3Store::new(config);
        Ok(Box::new(store))
    } else {
        Ok(Box::new(FileSystemStore::new(PathBuf::from(store_path))?))
    }
}

fn log_store_backend(store_config: &y_sweet_core::config::StoreConfig) {
    use y_sweet_core::config::StoreConfig;

    match store_config {
        StoreConfig::Memory => {
            tracing::info!("Store backend: memory (documents will not be persisted)");
        }
        StoreConfig::Filesystem(fs) => {
            tracing::info!("Store backend: filesystem at {}", fs.path);
        }
        other => {
            let kind = match other {
                StoreConfig::S3(_) => "s3",
                StoreConfig::Aws(_) => "aws",
                StoreConfig::Cloudflare(_) => "cloudflare",
                StoreConfig::Backblaze(_) => "backblaze",
                StoreConfig::Minio(_) => "minio",
                StoreConfig::Tigris(_) => "tigris",
                _ => "s3-compatible",
            };
            if let Some(s3) = other.to_s3_config() {
                let prefix = if s3.prefix.is_empty() {
                    "(no prefix)".to_string()
                } else {
                    format!("prefix={}", s3.prefix)
                };
                let endpoint = if s3.endpoint.is_empty() {
                    format!("region={}", s3.region)
                } else {
                    format!("endpoint={}", s3.endpoint)
                };
                tracing::info!(
                    "Store backend: {} bucket={} {} {}",
                    kind,
                    s3.bucket,
                    endpoint,
                    prefix,
                );
            } else {
                tracing::info!("Store backend: {}", kind);
            }
        }
    }
}

fn parse_allowed_hosts(hosts: Vec<String>) -> Result<Vec<AllowedHost>> {
    let mut parsed_hosts = Vec::new();

    for host_str in hosts {
        if host_str.starts_with("http://") || host_str.starts_with("https://") {
            let url = Url::parse(&host_str)
                .with_context(|| format!("Invalid URL in allowed hosts: {}", host_str))?;

            let host = url
                .host_str()
                .ok_or_else(|| anyhow::anyhow!("No host in URL: {}", host_str))?;

            parsed_hosts.push(AllowedHost {
                host: host.to_string(),
                scheme: url.scheme().to_string(),
            });
        } else {
            // Assume http for hosts without schemes
            parsed_hosts.push(AllowedHost {
                host: host_str,
                scheme: "http".to_string(),
            });
        }
    }

    Ok(parsed_hosts)
}

fn generate_allowed_hosts(
    url: Option<&Url>,
    explicit_hosts: Option<Vec<String>>,
    fly_app_name: Option<&str>,
) -> Result<Vec<AllowedHost>> {
    if let Some(hosts) = explicit_hosts {
        // Parse explicit hosts with schemes
        parse_allowed_hosts(hosts)
    } else if let Some(prefix) = url {
        // Auto-generate from url + flycast
        let mut hosts = vec![AllowedHost {
            host: prefix.host_str().unwrap().to_string(),
            scheme: prefix.scheme().to_string(),
        }];

        // Add flycast if app name is provided
        if let Some(app_name) = fly_app_name {
            hosts.push(AllowedHost {
                host: format!("{}.flycast", app_name),
                scheme: "http".to_string(),
            });
        }

        Ok(hosts)
    } else {
        Ok(vec![])
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let opts = Opts::parse();
    let mut loaded_serve_config = None;

    if let ServSubcommand::Serve {
        config,
        port,
        host,
        metrics_port,
        checkpoint_freq_seconds,
        store,
        auth,
        url,
        allowed_hosts,
        migrate: _,
    } = &opts.subcmd
    {
        loaded_serve_config = Some(load_config_for_serve_args(
            config.as_ref(),
            store,
            port,
            host,
            metrics_port,
            checkpoint_freq_seconds,
            auth,
            url,
            allowed_hosts,
        )?);
    }

    let filter = if let Some(config) = &loaded_serve_config {
        EnvFilter::try_new(&config.logging.level).with_context(|| {
            format!(
                "Invalid logging.level/RUST_LOG filter: {}",
                config.logging.level
            )
        })?
    } else {
        EnvFilter::builder()
            .with_default_directive(LevelFilter::INFO.into())
            .from_env_lossy()
    };
    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer())
        .with(filter)
        .init();

    match &opts.subcmd {
        ServSubcommand::Serve { migrate, .. } => {
            let config = loaded_serve_config
                .take()
                .expect("serve config should be loaded before tracing initialization");

            // Initialize logging based on config
            let log_level = &config.logging.level;
            tracing::info!("Using log level: {}", log_level);

            // Create authenticator from config
            let mut auth = if !config.auth.is_empty() {
                Some(Authenticator::from_multi_key_config(&config.auth)?)
            } else {
                tracing::warn!("No auth key set. Only use this for local development!");
                None
            };

            // Set expected audience for CWT validation if server URL is configured
            if let Some(ref mut authenticator) = auth {
                authenticator.set_expected_audience(config.server.url.clone());
                if let Some(ref url) = config.server.url {
                    tracing::info!("CWT audience validation enabled for: {}", url);
                } else {
                    return Err(anyhow::anyhow!("Server URL is required"));
                }
            }

            // Parse server host
            let server_host: IpAddr = config
                .server
                .host
                .parse()
                .unwrap_or(IpAddr::V4(Ipv4Addr::new(127, 0, 0, 1)));

            let addr = SocketAddr::new(server_host, config.server.port);
            let listener = TcpListener::bind(addr).await?;
            let addr = listener.local_addr()?;

            // Only bind metrics listener if metrics are enabled
            let metrics_listener_and_addr = if let Some(ref metrics_config) = config.metrics {
                let metrics_addr = SocketAddr::new(server_host, metrics_config.port);
                let metrics_listener = TcpListener::bind(metrics_addr).await?;
                let metrics_addr = metrics_listener.local_addr()?;
                Some((metrics_listener, metrics_addr))
            } else {
                None
            };

            // Run pending migrations first if requested. We build a
            // throwaway store handle for this so we don't have to share
            // ownership with the long-lived server store below.
            if *migrate {
                let migrate_store = get_store_from_config(&config.store)?.ok_or_else(|| {
                    anyhow::anyhow!(
                        "--migrate requires a persistent store; in-memory store has nothing to migrate"
                    )
                })?;
                relay::migrations::run_pending(Arc::new(migrate_store)).await?;
            }

            // Create store from config
            let store = if let Some(store) = get_store_from_config(&config.store)? {
                log_store_backend(&config.store);
                store.init().await?;
                Some(store)
            } else {
                tracing::warn!("No store set. Documents will be stored in memory only.");
                None
            };

            // Parse URL prefix
            let url = config
                .server
                .url
                .as_ref()
                .map(|s| Url::parse(s))
                .transpose()?;

            // Get FLY_APP_NAME once at configuration time to avoid race conditions
            let fly_app_name = env::var("FLY_APP_NAME").ok();

            // Generate allowed hosts (use config + auto-generation from URL prefix)
            let allowed_hosts = if config.server.allowed_hosts.is_empty() {
                // Auto-generate from url if no explicit hosts configured
                generate_allowed_hosts(url.as_ref(), None, fly_app_name.as_deref())?
            } else {
                // Use configured hosts, but also add Fly.io auto-detection if applicable
                let explicit_hosts: Vec<String> = config
                    .server
                    .allowed_hosts
                    .iter()
                    .map(|h| {
                        if h.scheme == "https" || h.scheme == "http" {
                            format!("{}://{}", h.scheme, h.host)
                        } else {
                            h.host.clone()
                        }
                    })
                    .collect();
                generate_allowed_hosts(url.as_ref(), Some(explicit_hosts), fly_app_name.as_deref())?
            };

            let token = CancellationToken::new();

            // Use webhook configs from configuration (TOML file or env vars)
            let webhook_configs = if config.webhooks.is_empty() {
                // Fallback to environment variable for backward compatibility
                relay::webhook::load_webhook_configs()
            } else {
                Some(config.webhooks.clone())
            };

            if let Some(ref configs) = webhook_configs {
                tracing::info!("Loaded {} webhook configurations", configs.len());
            }

            let server = relay::server::Server::new(
                store,
                std::time::Duration::from_secs(config.server.checkpoint_freq_seconds),
                auth,
                url.clone(),
                allowed_hosts,
                token.clone(),
                config.server.doc_gc,
                webhook_configs,
            )
            .await?;

            let redact_errors = config.server.redact_errors;
            let server = Arc::new(server);

            let main_handle = tokio::spawn({
                let server = server.clone();
                let token = token.clone();
                async move {
                    let routes = server.routes_with_metrics();
                    let app = routes.layer(middleware::from_fn(
                        relay::server::Server::version_header_middleware,
                    ));
                    let app = if redact_errors {
                        app.layer(middleware::from_fn(
                            relay::server::Server::redact_error_middleware,
                        ))
                    } else {
                        app
                    };
                    axum::serve(listener, app.into_make_service())
                        .with_graceful_shutdown(async move { token.cancelled().await })
                        .await
                        .unwrap();
                }
            });

            let metrics_addr_for_logging =
                metrics_listener_and_addr.as_ref().map(|(_, addr)| *addr);

            let metrics_handle = if let Some((metrics_listener, _)) = metrics_listener_and_addr {
                Some(tokio::spawn({
                    let server = server.clone();
                    let token = token.clone();
                    async move {
                        let metrics_routes = server.metrics_routes();
                        axum::serve(metrics_listener, metrics_routes.into_make_service())
                            .with_graceful_shutdown(async move { token.cancelled().await })
                            .await
                            .unwrap();
                    }
                }))
            } else {
                None
            };

            tracing::info!("Listening on ws://{}", addr);
            if let Some(metrics_addr) = metrics_addr_for_logging {
                tracing::info!("Metrics listening on http://{}", metrics_addr);
            } else {
                tracing::info!("Metrics disabled");
            }

            let signal = shutdown_signal().await;

            tracing::info!("Received {}, shutting down.", signal);
            token.cancel();

            if let Some(metrics_handle) = metrics_handle {
                let _ = tokio::join!(main_handle, metrics_handle);
            } else {
                let _ = main_handle.await;
            }
            tracing::info!("Server shut down.");
        }
        ServSubcommand::GenAuth { json, key_type } => {
            let auth = match key_type {
                KeyType::Legacy => Authenticator::gen_key_legacy()?,
                KeyType::Hmac256 => Authenticator::gen_key_hmac()?,
                KeyType::Es256 => Authenticator::gen_key_ecdsa()?,
                KeyType::EdDsa => Authenticator::gen_key_ed25519()?,
            };

            // Generate a key-id using nanoid
            let key_id = nanoid::nanoid!();

            if *json {
                let mut result = serde_json::Map::new();

                // Add key-id to output
                result.insert("key_id".to_string(), json!(key_id));

                // Generate appropriate server token based on key type
                match auth.key_material() {
                    y_sweet_core::auth::AuthKeyMaterial::Legacy(_) => {
                        // Generate legacy format server token
                        let server_token = auth.server_token_legacy()?;
                        result.insert("server_token".to_string(), json!(server_token));
                    }
                    _ => {
                        // Generate CWT server token for modern keys
                        let server_token = auth.server_token()?;
                        result.insert("server_token".to_string(), json!(server_token));
                    }
                };

                match auth.key_material() {
                    y_sweet_core::auth::AuthKeyMaterial::Legacy(key_bytes) => {
                        result.insert(
                            "private_key".to_string(),
                            json!(y_sweet_core::auth::b64_encode(key_bytes)),
                        );
                    }
                    y_sweet_core::auth::AuthKeyMaterial::Hmac256(key_bytes) => {
                        result.insert(
                            "private_key".to_string(),
                            json!(y_sweet_core::auth::b64_encode(key_bytes)),
                        );
                    }
                    y_sweet_core::auth::AuthKeyMaterial::EcdsaP256Private(key_bytes) => {
                        let private_key_b64 = y_sweet_core::auth::b64_encode(key_bytes);
                        result.insert("private_key".to_string(), json!(private_key_b64));

                        // Also generate and include public key
                        if let Ok(public_key) = generate_public_key_from_private(&private_key_b64) {
                            result.insert("public_key".to_string(), json!(public_key));
                        }
                    }
                    y_sweet_core::auth::AuthKeyMaterial::EcdsaP256Public(key_bytes) => {
                        result.insert(
                            "public_key".to_string(),
                            json!(y_sweet_core::auth::b64_encode(key_bytes)),
                        );
                        // No private_key field for public keys!
                    }
                    y_sweet_core::auth::AuthKeyMaterial::Ed25519Private(key_bytes) => {
                        let private_key_b64 = y_sweet_core::auth::b64_encode(key_bytes);
                        result.insert("private_key".to_string(), json!(private_key_b64));

                        // Also generate and include public key
                        if let Ok(public_key) =
                            generate_ed25519_public_key_from_private(&private_key_b64)
                        {
                            result.insert("public_key".to_string(), json!(public_key));
                        }
                    }
                    y_sweet_core::auth::AuthKeyMaterial::Ed25519Public(key_bytes) => {
                        result.insert(
                            "public_key".to_string(),
                            json!(y_sweet_core::auth::b64_encode(key_bytes)),
                        );
                        // No private_key field for public keys!
                    }
                }

                println!(
                    "{}",
                    serde_json::to_string_pretty(&serde_json::Value::Object(result))?
                );
            } else {
                println!("Key ID: {}", key_id);
                println!();
                print_auth_message(&auth);

                // Print additional info based on key type
                match auth.key_material() {
                    y_sweet_core::auth::AuthKeyMaterial::EcdsaP256Private(key_bytes) => {
                        let private_key_b64 = y_sweet_core::auth::b64_encode(key_bytes);
                        if let Ok(public_key) = generate_public_key_from_private(&private_key_b64) {
                            println!("Public key for ES256:");
                            println!("   {}", public_key);
                            println!();
                        }
                    }
                    y_sweet_core::auth::AuthKeyMaterial::EcdsaP256Public(_) => {
                        println!("Note: This is a public key - it can only verify tokens, not create them.");
                        println!();
                    }
                    y_sweet_core::auth::AuthKeyMaterial::Ed25519Private(key_bytes) => {
                        let private_key_b64 = y_sweet_core::auth::b64_encode(key_bytes);
                        if let Ok(public_key) =
                            generate_ed25519_public_key_from_private(&private_key_b64)
                        {
                            println!("Public key for EdDSA:");
                            println!("   {}", public_key);
                            println!();
                        }
                    }
                    y_sweet_core::auth::AuthKeyMaterial::Ed25519Public(_) => {
                        println!("Note: This is a public key - it can only verify tokens, not create them.");
                        println!();
                    }
                    _ => {}
                }
            }
        }
        ServSubcommand::ConvertFromUpdate { store, doc_id } => {
            let store = get_store_from_opts(store)?;
            store.init().await?;

            let mut stdin = tokio::io::stdin();
            let mut buf = Vec::new();
            stdin.read_to_end(&mut buf).await?;

            relay::convert::convert(store, &buf, doc_id).await?;
        }
        ServSubcommand::Version => {
            println!("{}", VERSION);
        }
        ServSubcommand::Config { cmd } => {
            match cmd {
                ConfigSubcommand::Validate { config } => {
                    println!("Validating configuration file: {}", config.display());

                    match Config::load(Some(config.as_path())) {
                        Ok(config) => {
                            println!("✅ Configuration is valid!");
                            println!();
                            println!("Configuration summary:");
                            println!("  Server: {}:{}", config.server.host, config.server.port);
                            if let Some(ref metrics_config) = config.metrics {
                                println!(
                                    "  Metrics: {}:{}",
                                    config.server.host, metrics_config.port
                                );
                            } else {
                                println!("  Metrics: disabled");
                            }
                            println!(
                                "  Auth: {}",
                                if config.auth.is_empty() {
                                    "disabled"
                                } else {
                                    "enabled"
                                }
                            );
                            println!(
                                "  Store: {}",
                                match &config.store {
                                    y_sweet_core::config::StoreConfig::Memory =>
                                        "Memory".to_string(),
                                    y_sweet_core::config::StoreConfig::Filesystem(fs) =>
                                        format!("Filesystem ({})", fs.path),
                                    y_sweet_core::config::StoreConfig::S3(s3) =>
                                        format!("S3 ({})", s3.bucket),
                                    y_sweet_core::config::StoreConfig::Aws(aws) =>
                                        format!("AWS S3 ({})", aws.bucket),
                                    y_sweet_core::config::StoreConfig::Cloudflare(cf) =>
                                        format!("Cloudflare R2 ({})", cf.bucket),
                                    y_sweet_core::config::StoreConfig::Backblaze(b2) =>
                                        format!("Backblaze B2 ({})", b2.bucket),
                                    y_sweet_core::config::StoreConfig::Minio(minio) =>
                                        format!("MinIO ({} at {})", minio.bucket, minio.endpoint),
                                    y_sweet_core::config::StoreConfig::Tigris(tigris) =>
                                        format!("Tigris ({})", tigris.bucket),
                                }
                            );
                            println!("  Webhooks: {}", config.webhooks.len());
                            println!(
                                "  Logging: {} ({})",
                                config.logging.level, config.logging.format
                            );

                            if let Some(url) = &config.server.url {
                                println!("  URL prefix: {}", url);
                            }

                            if !config.server.allowed_hosts.is_empty() {
                                println!("  Allowed hosts: {}", config.server.allowed_hosts.len());
                                for host in &config.server.allowed_hosts {
                                    println!("    - {}://{}", host.scheme, host.host);
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("❌ Configuration validation failed:");
                            eprintln!("   {}", e);
                            std::process::exit(1);
                        }
                    }
                }
                ConfigSubcommand::Show { config } => {
                    match Config::load(Some(config.as_path())) {
                        Ok(config) => {
                            // Print environment variables to stderr first
                            config.print_env();

                            println!("Current configuration:");
                            println!();

                            // Convert to regular TOML for display
                            match toml::to_string_pretty(&config) {
                                Ok(toml_str) => println!("{}", toml_str),
                                Err(e) => {
                                    eprintln!("❌ Failed to serialize configuration: {}", e);
                                    std::process::exit(1);
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("❌ Failed to load configuration:");
                            eprintln!("   {}", e);
                            std::process::exit(1);
                        }
                    }
                }
            }
        }
        ServSubcommand::Migrate { config, store } => {
            let store = build_store_for_subcommand(store.as_deref(), config.as_ref())?;
            relay::migrations::run_pending(store).await?;
        }
        ServSubcommand::Sign { auth } => {
            let authenticator = Authenticator::new(auth)?;
            sign_stdin(&authenticator).await?;
        }
        ServSubcommand::Verify {
            auth,
            doc_id,
            file_hash,
        } => {
            let authenticator = Authenticator::new(auth)?;
            // Use the doc_id if provided, otherwise use file_hash if provided
            let id = doc_id.as_deref().or(file_hash.as_deref());
            verify_stdin(&authenticator, id).await?;
        }

        ServSubcommand::Subdocs { cmd } => match cmd {
            SubdocsCommand::Index {
                config,
                store,
                relay_id,
                folder,
                check,
                dry_run,
            } => {
                let store = build_store_for_subcommand(store.as_deref(), config.as_ref())?;
                relay::subdocs::run_backfill(store, relay_id, folder, *check, *dry_run).await?;
            }
        },

        ServSubcommand::Doc { cmd } => match cmd {
            DocCommand::Inspect { cmd } => match cmd {
                DocInspectCommand::Info { input, keys } => {
                    let (label, bytes) = resolve_inspect_input(input).await?;
                    relay::doc_inspect::run_info(&label, &bytes, *keys)?;
                }
                DocInspectCommand::Users { input } => {
                    let (label, bytes) = resolve_inspect_input(input).await?;
                    relay::doc_inspect::run_users(&label, &bytes)?;
                }
                DocInspectCommand::History { input } => {
                    let (label, bytes) = resolve_inspect_input(input).await?;
                    relay::doc_inspect::run_history(&label, &bytes)?;
                }
            },
            DocCommand::Versions {
                config,
                store,
                relay_id,
                doc,
                limit,
            } => {
                let store = build_store_for_subcommand(store.as_deref(), config.as_ref())?;
                let limit = if *limit == 0 { None } else { Some(*limit) };
                relay::doc_versions::run(store, relay_id, doc, limit).await?;
            }
            DocCommand::Get {
                config,
                store,
                relay_id,
                doc,
                version,
                output,
            } => {
                let store = build_store_for_subcommand(store.as_deref(), config.as_ref())?;
                store.init().await.context("store init failed")?;
                let data_key = format!("{}-{}/data.ysweet", relay_id, doc);
                let bytes = match version {
                    Some(vid) => store
                        .get_version(&data_key, vid)
                        .await
                        .with_context(|| format!("get_version failed for {} @ {}", data_key, vid))?
                        .ok_or_else(|| {
                            anyhow::anyhow!("no object at {} version {}", data_key, vid)
                        })?,
                    None => store
                        .get(&data_key)
                        .await
                        .with_context(|| format!("get failed for {}", data_key))?
                        .ok_or_else(|| anyhow::anyhow!("no object at {}", data_key))?,
                };

                match output {
                    Some(path) => {
                        std::fs::write(path, &bytes)
                            .with_context(|| format!("failed to write {}", path.display()))?;
                        eprintln!("wrote {} bytes to {}", bytes.len(), path.display());
                    }
                    None => {
                        use std::io::Write;
                        std::io::stdout().write_all(&bytes)?;
                    }
                }
            }
            DocCommand::Restore { args } => {
                let DocRestoreArgs {
                    config,
                    store,
                    relay_id,
                    doc,
                    from_version,
                    only,
                    except,
                    write,
                    verify,
                } = args;
                let store = build_store_for_subcommand(store.as_deref(), config.as_ref())?;
                relay::doc_restore::run(
                    store,
                    relay_id,
                    doc,
                    from_version,
                    only,
                    except,
                    *write,
                    *verify,
                )
                .await?;
            }
        },
    }

    Ok(())
}

/// Resolve when the process receives a shutdown signal: SIGTERM (what
/// process supervisors and container platforms send on stop/deploy) or
/// Ctrl+C/SIGINT (interactive use).
async fn shutdown_signal() -> &'static str {
    let ctrl_c = tokio::signal::ctrl_c();

    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut signal) => {
                signal.recv().await;
            }
            Err(e) => {
                tracing::error!("Failed to install SIGTERM handler: {}", e);
                std::future::pending::<()>().await
            }
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => "SIGINT",
        _ = terminate => "SIGTERM",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_allowed_hosts() {
        let hosts = vec![
            "https://api.example.com".to_string(),
            "http://app.flycast".to_string(),
            "localhost".to_string(),
        ];

        let parsed = parse_allowed_hosts(hosts).unwrap();

        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0].host, "api.example.com");
        assert_eq!(parsed[0].scheme, "https");
        assert_eq!(parsed[1].host, "app.flycast");
        assert_eq!(parsed[1].scheme, "http");
        assert_eq!(parsed[2].host, "localhost");
        assert_eq!(parsed[2].scheme, "http");
    }

    #[test]
    fn test_generate_allowed_hosts_explicit() {
        let explicit_hosts = Some(vec![
            "https://api.example.com".to_string(),
            "http://app.flycast".to_string(),
        ]);

        let hosts = generate_allowed_hosts(None, explicit_hosts, None).unwrap();

        assert_eq!(hosts.len(), 2);
        assert_eq!(hosts[0].host, "api.example.com");
        assert_eq!(hosts[0].scheme, "https");
        assert_eq!(hosts[1].host, "app.flycast");
        assert_eq!(hosts[1].scheme, "http");
    }

    #[test]
    fn test_generate_allowed_hosts_from_prefix() {
        let url: Url = "https://api.example.com".parse().unwrap();

        // Without FLY_APP_NAME
        let hosts = generate_allowed_hosts(Some(&url), None, None).unwrap();

        assert_eq!(hosts.len(), 1);
        assert_eq!(hosts[0].host, "api.example.com");
        assert_eq!(hosts[0].scheme, "https");

        // With FLY_APP_NAME
        let hosts = generate_allowed_hosts(Some(&url), None, Some("my-app")).unwrap();

        assert_eq!(hosts.len(), 2);
        assert_eq!(hosts[0].host, "api.example.com");
        assert_eq!(hosts[0].scheme, "https");
        assert_eq!(hosts[1].host, "my-app.flycast");
        assert_eq!(hosts[1].scheme, "http");
    }

    #[test]
    fn test_generate_allowed_hosts_empty() {
        let hosts = generate_allowed_hosts(None, None, None).unwrap();
        assert_eq!(hosts.len(), 0);
    }

    #[test]
    fn test_fly_io_scenario() {
        // Simulate a Fly.io deployment scenario
        let url: Url = "https://api.mycompany.com".parse().unwrap();
        let hosts = generate_allowed_hosts(Some(&url), None, Some("my-relay-server")).unwrap();

        // Should have both external and internal hosts
        assert_eq!(hosts.len(), 2);

        // External host for public access
        assert_eq!(hosts[0].host, "api.mycompany.com");
        assert_eq!(hosts[0].scheme, "https");

        // Internal flycast host for internal access
        assert_eq!(hosts[1].host, "my-relay-server.flycast");
        assert_eq!(hosts[1].scheme, "http");
    }
}
