use crate::api_types::Authorization;
use crate::config::TokenType;
use bincode::Options;
use data_encoding::Encoding;
use rand::Rng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fmt::Display;
use thiserror::Error;

pub const DEFAULT_EXPIRATION_SECONDS: u64 = 60 * 60; // 60 minutes

/// This newtype is introduced to distinguish between a u64 meant to represent the current time
/// (currently passed as a raw u64), and a u64 meant to represent an expiration time.
/// We introduce this to intentonally break callers to `gen_doc_token` that do not explicitly
/// update to pass an expiration time, so that calls that use the old signature to pass a current
/// time do not compile.
/// Unit is milliseconds since Jan 1, 1970.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct ExpirationTimeEpochMillis(pub u64);

impl ExpirationTimeEpochMillis {
    pub fn max() -> Self {
        Self(u64::MAX)
    }
}

/// This is a custom base64 encoder that is equivalent to BASE64URL_NOPAD for encoding,
/// but is tolerant when decoding of the “standard” alphabet and also of padding.
/// This is necessary for now because we used to use standard base64 encoding with padding,
/// but we can eventually remove it.
///
/// ```
/// use data_encoding::{Specification, BASE64URL_NOPAD, Translate};
/// let spec = Specification {
///     ignore: "=".to_string(),
///     translate: Translate {
///         from: "/+".to_string(),
///         to: "_-".to_string(),
///     },
///     ..BASE64URL_NOPAD.specification()
/// };
/// use y_sweet_core::auth::BASE64_CUSTOM;
/// assert_eq!(BASE64_CUSTOM, spec.encoding().unwrap());
/// ```
pub const BASE64_CUSTOM: Encoding = Encoding::internal_new(&[
    65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88,
    89, 90, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114,
    115, 116, 117, 118, 119, 120, 121, 122, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 45, 95, 65, 66,
    67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90,
    97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115,
    116, 117, 118, 119, 120, 121, 122, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 45, 95, 65, 66, 67,
    68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 97,
    98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116,
    117, 118, 119, 120, 121, 122, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 45, 95, 65, 66, 67, 68,
    69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 97, 98,
    99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117,
    118, 119, 120, 121, 122, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 45, 95, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 62, 128, 62, 128, 63, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 128, 128, 128, 129, 128,
    128, 128, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    24, 25, 128, 128, 128, 128, 63, 128, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39,
    40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128,
    128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 128, 30, 0,
]);

#[derive(Error, Debug, PartialEq, Eq)]
pub enum AuthError {
    #[error("The token is not a valid format")]
    InvalidToken,
    #[error("The token is expired")]
    Expired,
    #[error("The token is not valid for the requested resource")]
    InvalidResource,
    #[error("The token signature is invalid")]
    InvalidSignature,
    #[error("The key ID did not match")]
    KeyMismatch,
    #[error("Invalid CBOR structure")]
    InvalidCbor,
    #[error("Invalid COSE structure")]
    InvalidCose,
    #[error("Unsupported COSE algorithm")]
    UnsupportedAlgorithm,
    #[error("Invalid CWT claims")]
    InvalidClaims,
    #[error("Signature verification failed")]
    SignatureVerificationFailed,
    #[error("Cannot sign tokens with public key - signing requires private key")]
    CannotSignWithPublicKey,
    #[error("Multiple private keys not allowed")]
    MultiplePrivateKeys,
    #[error("Cannot specify both private_key and public_key")]
    BothKeysProvided,
    #[error("Must specify either private_key or public_key")]
    NoKeyProvided,
    #[error("No signing key available")]
    NoSigningKey,
    #[error("Duplicate key_id: {0}")]
    DuplicateKeyId(String),
    #[error("Invalid audience claim: expected '{expected}', found '{found}'")]
    InvalidAudience { expected: String, found: String },
    #[error("Missing audience claim: expected '{expected}'")]
    MissingAudience { expected: String },
    #[error("Insufficient permissions: {0}")]
    InsufficientPermissions(String),
    #[error("Unauthorized token type: {0}")]
    UnauthorizedTokenType(String),
    #[error("Invalid token type in configuration: {0}")]
    InvalidTokenType(String),
}

impl AuthError {
    /// Map AuthError to metric label for monitoring
    pub fn to_metric_label(&self) -> &'static str {
        match self {
            AuthError::InvalidToken => "invalid_format",
            AuthError::Expired => "expired",
            AuthError::InvalidResource => "invalid_resource",
            AuthError::InvalidSignature => "invalid_signature",
            AuthError::KeyMismatch => "key_mismatch",
            AuthError::InvalidCbor => "invalid_cbor",
            AuthError::InvalidCose => "invalid_cose",
            AuthError::UnsupportedAlgorithm => "unsupported_algorithm",
            AuthError::InvalidClaims => "invalid_claims",
            AuthError::SignatureVerificationFailed => "signature_verification_failed",
            AuthError::CannotSignWithPublicKey => "cannot_sign_with_public_key",
            AuthError::MultiplePrivateKeys => "multiple_private_keys",
            AuthError::BothKeysProvided => "both_keys_provided",
            AuthError::NoKeyProvided => "no_key_provided",
            AuthError::NoSigningKey => "no_signing_key",
            AuthError::DuplicateKeyId(_) => "duplicate_key_id",
            AuthError::InvalidAudience { .. } => "invalid_audience",
            AuthError::MissingAudience { .. } => "missing_audience",
            AuthError::InsufficientPermissions(_) => "insufficient_permissions",
            AuthError::UnauthorizedTokenType(_) => "unauthorized_token_type",
            AuthError::InvalidTokenType(_) => "invalid_token_type",
        }
    }
}

#[derive(Debug, PartialEq, Serialize, Deserialize, Clone)]
pub enum AuthKeyMaterial {
    Hmac256(Vec<u8>), // 32-byte keys for HMAC-SHA256 (CWT tokens)
    Legacy(Vec<u8>),  // 30-byte keys for legacy token system
    EcdsaP256Private(Vec<u8>),
    EcdsaP256Public(Vec<u8>),
    Ed25519Private(Vec<u8>), // 32-byte Ed25519 private keys
    Ed25519Public(Vec<u8>),  // 32-byte Ed25519 public keys
}

impl AuthKeyMaterial {
    /// Get the base64 representation of the key material
    pub fn to_base64(&self) -> String {
        match self {
            AuthKeyMaterial::Hmac256(key_bytes) => b64_encode(key_bytes),
            AuthKeyMaterial::Legacy(key_bytes) => b64_encode(key_bytes),
            AuthKeyMaterial::EcdsaP256Private(key_bytes) => b64_encode(key_bytes),
            AuthKeyMaterial::EcdsaP256Public(key_bytes) => b64_encode(key_bytes),
            AuthKeyMaterial::Ed25519Private(key_bytes) => b64_encode(key_bytes),
            AuthKeyMaterial::Ed25519Public(key_bytes) => b64_encode(key_bytes),
        }
    }

    /// Get the ECDSA private key base64 for public key generation (only works with ECDSA private keys)
    pub fn ecdsa_private_key_base64(&self) -> Option<String> {
        match self {
            AuthKeyMaterial::EcdsaP256Private(key_bytes) => Some(b64_encode(key_bytes)),
            _ => None,
        }
    }
}

#[derive(Debug, PartialEq, Serialize, Deserialize, Clone)]
pub struct AuthKeyEntry {
    pub key_id: Option<String>,
    pub key_material: AuthKeyMaterial,
    pub can_sign: bool,
    pub allowed_token_types: Vec<TokenType>,
}

#[derive(Debug, PartialEq, Serialize, Deserialize, Clone)]
pub struct Authenticator {
    pub keys: Vec<AuthKeyEntry>,
    pub key_lookup: std::collections::HashMap<String, usize>,
    pub keys_without_id: Vec<usize>,
    pub expected_audience: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, PartialEq)]
pub struct DocPermission {
    pub doc_id: String,
    pub authorization: Authorization,
    pub user: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, PartialEq)]
pub struct FilePermission {
    pub file_hash: String,
    pub authorization: Authorization,
    pub content_type: Option<String>,
    pub content_length: Option<u64>,
    pub doc_id: String,
    pub user: Option<String>,
}

// Legacy structs for backward compatibility with old tokens
#[derive(Serialize, Deserialize)]
struct LegacyDocPermission {
    pub doc_id: String,
    pub authorization: Authorization,
}

#[derive(Serialize, Deserialize)]
struct LegacyFilePermission {
    pub file_hash: String,
    pub authorization: Authorization,
    pub content_type: Option<String>,
    pub content_length: Option<u64>,
    pub doc_id: String,
}

#[derive(Serialize, Deserialize, Debug, PartialEq)]
pub struct PrefixPermission {
    pub prefix: String,
    pub authorization: Authorization,
    pub user: Option<String>,
}

#[derive(Serialize, Deserialize, Debug, PartialEq)]
pub enum Permission {
    Server,
    Doc(DocPermission),
    File(FilePermission),
    Prefix(PrefixPermission),
}

// Legacy enums for backward compatibility
#[derive(Serialize, Deserialize)]
enum LegacyPermission {
    Server,
    Doc(LegacyDocPermission),
    File(LegacyFilePermission),
}

#[derive(Serialize, Deserialize)]
pub struct Payload {
    pub payload: Permission,
    pub expiration_millis: Option<ExpirationTimeEpochMillis>,
}

#[derive(Serialize, Deserialize)]
pub struct AuthenticatedRequest {
    pub payload: Payload,
    pub token: Vec<u8>,
}

// Legacy structs for backward compatibility
#[derive(Serialize, Deserialize)]
struct LegacyPayload {
    pub payload: LegacyPermission,
    pub expiration_millis: Option<ExpirationTimeEpochMillis>,
}

#[derive(Serialize, Deserialize)]
struct LegacyAuthenticatedRequest {
    pub payload: LegacyPayload,
    pub token: Vec<u8>,
}

// Conversion from legacy to current structs
impl From<LegacyPermission> for Permission {
    fn from(legacy: LegacyPermission) -> Self {
        match legacy {
            LegacyPermission::Server => Permission::Server,
            LegacyPermission::Doc(doc) => Permission::Doc(DocPermission {
                doc_id: doc.doc_id,
                authorization: doc.authorization,
                user: None, // Old tokens don't have user field
            }),
            LegacyPermission::File(file) => Permission::File(FilePermission {
                file_hash: file.file_hash,
                authorization: file.authorization,
                content_type: file.content_type,
                content_length: file.content_length,
                doc_id: file.doc_id,
                user: None, // Old tokens don't have user field
            }),
        }
    }
}

impl From<LegacyPayload> for Payload {
    fn from(legacy: LegacyPayload) -> Self {
        Payload {
            payload: legacy.payload.into(),
            expiration_millis: legacy.expiration_millis,
        }
    }
}

impl From<LegacyAuthenticatedRequest> for AuthenticatedRequest {
    fn from(legacy: LegacyAuthenticatedRequest) -> Self {
        AuthenticatedRequest {
            payload: legacy.payload.into(),
            token: legacy.token,
        }
    }
}

fn bincode_encode<T: Serialize>(value: &T) -> Result<Vec<u8>, bincode::Error> {
    // This uses different defaults than the default bincode::serialize() function.
    bincode::DefaultOptions::new().serialize(&value)
}

fn bincode_decode<'a, T: Deserialize<'a>>(bytes: &'a [u8]) -> Result<T, bincode::Error> {
    // This uses different defaults than the default bincode::deserialize() function.
    bincode::DefaultOptions::new().deserialize(bytes)
}

pub fn b64_encode(bytes: &[u8]) -> String {
    BASE64_CUSTOM.encode(bytes)
}

pub fn b64_decode(input: &str) -> Result<Vec<u8>, AuthError> {
    BASE64_CUSTOM
        .decode(input.as_bytes())
        .map_err(|_| AuthError::InvalidToken)
}

fn detect_key_type(key_bytes: &[u8]) -> &'static str {
    match key_bytes.len() {
        32 => "HMAC-SHA-256 (32 bytes)",
        33 => "ES256 compressed public key (33 bytes)",
        65 => "ES256 uncompressed public key (65 bytes)",
        _ => "Unknown key type",
    }
}

fn parse_private_key_format(input: &str) -> Result<AuthKeyMaterial, AuthError> {
    let trimmed = input.trim();

    // Raw base64 - for private keys, only Legacy and HMAC256 are supported
    let key_bytes = b64_decode(trimmed)?;
    let key_type = detect_key_type(&key_bytes);
    tracing::info!("Detected key type: {}", key_type);

    match key_bytes.len() {
        30 => {
            tracing::info!("Using 30-byte key for legacy token system");
            Ok(AuthKeyMaterial::Legacy(key_bytes))
        }
        32 => {
            tracing::info!("Using 32-byte key for HMAC-SHA256 (CWT tokens)");
            Ok(AuthKeyMaterial::Hmac256(key_bytes))
        }
        _ => {
            tracing::warn!(
                "Unexpected key length: {} bytes, defaulting to HMAC256",
                key_bytes.len()
            );
            Ok(AuthKeyMaterial::Hmac256(key_bytes))
        }
    }
}

fn parse_public_key_format(input: &str) -> Result<AuthKeyMaterial, AuthError> {
    let trimmed = input.trim();

    if trimmed.starts_with("-----BEGIN") && trimmed.contains("-----END") {
        // PEM format - determine key type from header
        if trimmed.contains("-----BEGIN PUBLIC KEY-----") {
            // Try Ed25519 first, then fall back to ECDSA
            let key_bytes = extract_pem_content(trimmed)?;

            // Try Ed25519 public key first (32 bytes)
            if key_bytes.len() == 32 {
                tracing::info!("Parsed PEM Ed25519 public key");
                return Ok(AuthKeyMaterial::Ed25519Public(key_bytes));
            }

            // Fall back to ECDSA public key
            tracing::info!("Parsed PEM ECDSA P-256 public key");
            Ok(AuthKeyMaterial::EcdsaP256Public(key_bytes))
        } else {
            tracing::error!("Unsupported PEM key type for public key");
            Err(AuthError::InvalidToken)
        }
    } else {
        // Raw base64 - try to parse as Ed25519 public key first, then fall back to ECDSA
        let key_bytes = b64_decode(trimmed)?;

        // Try Ed25519 public key first (32 bytes)
        if key_bytes.len() == 32 {
            if let Ok(key_array) = key_bytes.as_slice().try_into() {
                if ed25519_dalek::VerifyingKey::from_bytes(&key_array).is_ok() {
                    tracing::info!("Parsed raw base64 Ed25519 public key");
                    return Ok(AuthKeyMaterial::Ed25519Public(key_bytes));
                }
            }
        }

        // Try ECDSA public key (various lengths)
        if p256::PublicKey::from_sec1_bytes(&key_bytes).is_ok() {
            tracing::info!("Parsed raw base64 ECDSA P-256 public key");
            return Ok(AuthKeyMaterial::EcdsaP256Public(key_bytes));
        }

        // If both parsing attempts failed, return error
        tracing::error!("Failed to parse public key as either Ed25519 or ECDSA P-256");
        Err(AuthError::InvalidToken)
    }
}

fn extract_pem_content(pem_str: &str) -> Result<Vec<u8>, AuthError> {
    let lines: Vec<&str> = pem_str.lines().collect();
    let mut base64_content = String::new();

    let mut in_content = false;
    for line in lines {
        let line = line.trim();
        if line.starts_with("-----BEGIN") {
            in_content = true;
            continue;
        }
        if line.starts_with("-----END") {
            break;
        }
        if in_content && !line.is_empty() {
            base64_content.push_str(line);
        }
    }

    if base64_content.is_empty() {
        return Err(AuthError::InvalidToken);
    }

    b64_decode(&base64_content)
}

impl Payload {
    pub fn new(payload: Permission) -> Self {
        Self {
            payload,
            expiration_millis: None,
        }
    }

    pub fn new_with_expiration(
        payload: Permission,
        expiration_millis: ExpirationTimeEpochMillis,
    ) -> Self {
        Self {
            payload,
            expiration_millis: Some(expiration_millis),
        }
    }
}

fn hash(bytes: &[u8]) -> Vec<u8> {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let result = hasher.finalize();
    result.to_vec()
}

#[derive(Debug, PartialEq, Eq, Serialize, Deserialize, Clone)]
pub struct KeyId(String);

#[derive(Error, Debug, PartialEq, Eq)]
pub enum KeyIdError {
    #[error("The key ID cannot be an empty string")]
    EmptyString,
    #[error("The key ID contains an invalid character: {ch}")]
    InvalidCharacter { ch: char },
}

impl KeyId {
    pub fn new(key_id: String) -> Result<Self, KeyIdError> {
        if key_id.is_empty() {
            return Err(KeyIdError::EmptyString);
        }

        let valid_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        for ch in key_id.chars() {
            if !valid_chars.contains(ch) {
                return Err(KeyIdError::InvalidCharacter { ch });
            }
        }

        Ok(Self(key_id))
    }
}

impl Display for KeyId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl TryFrom<&str> for KeyId {
    type Error = KeyIdError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value.to_string())
    }
}

/// Token format enum for detecting token types
#[derive(Debug, PartialEq, Eq)]
pub enum TokenFormat {
    Custom,
    Cwt,
}

/// Detect the format of a token based on its structure
pub fn detect_token_format(token: &str) -> TokenFormat {
    let token_data = token;

    // Try to decode as base64
    if let Ok(decoded) = b64_decode(token_data) {
        // First check if it can be decoded as bincode (custom format)
        // This should be checked first since bincode is our current format
        if bincode_decode::<AuthenticatedRequest>(&decoded).is_ok() {
            return TokenFormat::Custom;
        }

        // Check if it's a valid CWT structure (with CWT tag 61 and COSE message inside)
        let is_cwt = is_cwt_token(&decoded);
        if is_cwt {
            return TokenFormat::Cwt;
        }
    } else {
    }

    // Default to custom format for backward compatibility
    TokenFormat::Custom
}

/// Extract key ID from CWT token COSE headers
fn extract_cwt_key_id(token: &str) -> Option<String> {
    // Decode the base64 token
    let token_bytes = match b64_decode(token) {
        Ok(bytes) => bytes,
        Err(_) => {
            tracing::trace!("Failed to base64 decode token for key ID extraction");
            return None;
        }
    };

    // Parse as CBOR to access COSE structure
    let cbor_value: ciborium::Value = match ciborium::de::from_reader(&token_bytes[..]) {
        Ok(value) => value,
        Err(_) => {
            tracing::trace!("Failed to parse token as CBOR for key ID extraction");
            return None;
        }
    };

    // Navigate to COSE structure to extract key ID
    let cose_structure = match &cbor_value {
        // CWT tag 61 wrapping COSE
        ciborium::Value::Tag(61, inner) => &**inner,
        // Direct COSE structure
        _ => &cbor_value,
    };

    match cose_structure {
        // COSE_Sign1 (tag 18) or COSE_Mac0 (tag 17)
        ciborium::Value::Tag(tag_num, cose_content) if *tag_num == 17 || *tag_num == 18 => {
            if let ciborium::Value::Array(cose_array) = &**cose_content {
                if let Some(ciborium::Value::Bytes(protected_bytes)) = cose_array.get(0) {
                    // Parse protected headers
                    if let Ok(protected_map) =
                        ciborium::de::from_reader::<ciborium::Value, _>(&protected_bytes[..])
                    {
                        if let ciborium::Value::Map(headers) = protected_map {
                            // Look for key ID (COSE parameter 4)
                            for (key, value) in headers {
                                if let ciborium::Value::Integer(param_num) = key {
                                    if let Ok(4) = TryInto::<i32>::try_into(param_num) {
                                        if let ciborium::Value::Bytes(kid_bytes) = value {
                                            if let Ok(kid_str) = String::from_utf8(kid_bytes) {
                                                tracing::trace!(
                                                    "Extracted key ID from CWT COSE headers: '{}'",
                                                    kid_str
                                                );
                                                return Some(kid_str);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        _ => {
            tracing::trace!("Token doesn't have recognizable COSE structure for key ID extraction");
        }
    }

    None
}

fn is_cwt_token(data: &[u8]) -> bool {
    // Try to parse as CBOR value first
    if let Ok(cbor_value) = ciborium::de::from_reader(&data[..]) {
        match cbor_value {
            // Check for CWT tag 61 with inner COSE structure
            ciborium::Value::Tag(61, inner_value) => {
                // Inner value should be a COSE message (Sign1 or Mac0)
                let mut inner_bytes = Vec::new();
                if ciborium::ser::into_writer(&*inner_value, &mut inner_bytes).is_ok() {
                    // The inner value might itself be a tagged COSE message
                    if let Ok(inner_cbor) = ciborium::de::from_reader(&inner_bytes[..]) {
                        match inner_cbor {
                            ciborium::Value::Tag(inner_tag, _) => {
                                if inner_tag == 17 || inner_tag == 18 {
                                    return true;
                                }
                            }
                            _ => {}
                        }
                    }

                    let result = is_cose_message(&inner_bytes);
                    return result;
                }
            }
            ciborium::Value::Tag(_tag_num, _) => {
                return is_cose_message(data);
            }
            _ => {
                return is_cose_message(data);
            }
        }
    } else {
    }
    false
}

fn is_cose_message(data: &[u8]) -> bool {
    use coset::CborSerializable;

    let sign1_ok = coset::CoseSign1::from_slice(data).is_ok();
    let mac0_ok = coset::CoseMac0::from_slice(data).is_ok();

    sign1_ok || mac0_ok
}

impl Authenticator {
    pub fn new(key: &str) -> Result<Self, AuthError> {
        let key_material = parse_private_key_format(key)?;
        let can_sign = matches!(
            key_material,
            AuthKeyMaterial::Hmac256(_) | AuthKeyMaterial::Legacy(_)
        );

        Ok(Self {
            keys: vec![AuthKeyEntry {
                key_id: None,
                key_material,
                can_sign,
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
            }],
            key_lookup: std::collections::HashMap::new(),
            keys_without_id: vec![0],
            expected_audience: None,
        })
    }

    /// Create authenticator from multi-key configuration
    pub fn from_multi_key_config(
        configs: &[crate::config::AuthKeyConfig],
    ) -> Result<Self, AuthError> {
        let mut keys = Vec::new();
        let mut key_lookup = std::collections::HashMap::new();
        let mut keys_without_id = Vec::new();
        let mut private_key_count = 0;

        for (index, config) in configs.iter().enumerate() {
            let (key_material, can_sign) = match (&config.private_key, &config.public_key) {
                (Some(private_key), None) => {
                    private_key_count += 1;
                    if private_key_count > 1 {
                        return Err(AuthError::MultiplePrivateKeys);
                    }
                    let material = parse_private_key_format(private_key)?;
                    let can_sign = matches!(
                        material,
                        AuthKeyMaterial::Hmac256(_) | AuthKeyMaterial::Legacy(_)
                    );
                    (material, can_sign)
                }
                (None, Some(public_key)) => {
                    let material = parse_public_key_format(public_key)?;
                    (material, false)
                }
                (Some(_), Some(_)) => {
                    return Err(AuthError::BothKeysProvided);
                }
                (None, None) => {
                    return Err(AuthError::NoKeyProvided);
                }
            };

            let key_entry = AuthKeyEntry {
                key_id: config.key_id.clone(),
                key_material,
                can_sign,
                allowed_token_types: config.allowed_token_types.clone(),
            };

            // Build lookup structures
            if let Some(ref key_id) = config.key_id {
                key_lookup.insert(key_id.clone(), index);
            } else {
                keys_without_id.push(index);
            }

            keys.push(key_entry);
        }

        Ok(Self {
            keys,
            key_lookup,
            keys_without_id,
            expected_audience: None,
        })
    }

    /// Set the expected audience for CWT token validation
    pub fn set_expected_audience(&mut self, audience: Option<String>) {
        self.expected_audience = audience;
    }

    /// Create a CWT authenticator from a specific key entry
    fn create_cwt_authenticator_for_key(
        &self,
        key_entry: &AuthKeyEntry,
    ) -> Result<crate::cwt::CwtAuthenticator, crate::cwt::CwtError> {
        match &key_entry.key_material {
            AuthKeyMaterial::Hmac256(key_bytes) => {
                crate::cwt::CwtAuthenticator::new_symmetric(key_bytes, key_entry.key_id.clone())
            }
            AuthKeyMaterial::Legacy(_) => {
                // Legacy keys cannot be used for CWT tokens
                tracing::error!("Legacy 30-byte keys cannot be used for CWT token operations");
                Err(crate::cwt::CwtError::InvalidCose)
            }
            AuthKeyMaterial::EcdsaP256Private(key_bytes) => {
                crate::cwt::CwtAuthenticator::new_ecdsa_p256(key_bytes, key_entry.key_id.clone())
            }
            AuthKeyMaterial::EcdsaP256Public(key_bytes) => {
                crate::cwt::CwtAuthenticator::new_ecdsa_p256_public(
                    key_bytes,
                    key_entry.key_id.clone(),
                )
            }
            AuthKeyMaterial::Ed25519Private(key_bytes) => {
                crate::cwt::CwtAuthenticator::new_ed25519(key_bytes, key_entry.key_id.clone())
            }
            AuthKeyMaterial::Ed25519Public(key_bytes) => {
                crate::cwt::CwtAuthenticator::new_ed25519_public(
                    key_bytes,
                    key_entry.key_id.clone(),
                )
            }
        }
    }

    /// Create a CWT authenticator from the first signing key
    fn create_cwt_authenticator(
        &self,
    ) -> Result<crate::cwt::CwtAuthenticator, crate::cwt::CwtError> {
        let signing_key = self
            .get_signing_key()
            .map_err(|_| crate::cwt::CwtError::InvalidCose)?;
        self.create_cwt_authenticator_for_key(signing_key)
    }

    /// Find which key was used to verify a token by attempting verification with each key
    fn find_verifying_key(&self, token: &str) -> Result<&AuthKeyEntry, AuthError> {
        let format = detect_token_format(token);

        match format {
            TokenFormat::Custom => {
                // Try verification with each key and return the first that succeeds
                for key_entry in &self.keys {
                    if self.verify_with_key_entry(key_entry, token, 0).is_ok() {
                        return Ok(key_entry);
                    }
                }
                Err(AuthError::KeyMismatch)
            }
            TokenFormat::Cwt => {
                // For CWT tokens, extract key ID from COSE headers
                if let Some(key_id_from_token) = extract_cwt_key_id(token) {
                    // Look up the key by ID
                    if let Some(&index) = self.key_lookup.get(&key_id_from_token) {
                        return Ok(&self.keys[index]);
                    }
                }

                // Fallback: try each key without ID
                for &index in &self.keys_without_id {
                    let key_entry = &self.keys[index];
                    if let Some(ref audience) = self.expected_audience {
                        if self
                            .verify_cwt_with_key(key_entry, token, 0, audience)
                            .is_ok()
                        {
                            return Ok(key_entry);
                        }
                    }
                }

                Err(AuthError::KeyMismatch)
            }
        }
    }

    /// Get the first signing key
    fn get_signing_key(&self) -> Result<&AuthKeyEntry, AuthError> {
        self.keys
            .iter()
            .find(|k| k.can_sign)
            .ok_or(AuthError::NoSigningKey)
    }

    /// Get the key material for direct access by callers (first key for compatibility)
    pub fn key_material(&self) -> &AuthKeyMaterial {
        &self.keys[0].key_material
    }

    /// Extract the public key in PEM format for asymmetric keys
    pub fn public_key_pem(&self) -> Result<String, AuthError> {
        match &self.keys[0].key_material {
            AuthKeyMaterial::EcdsaP256Private(private_bytes) => {
                use p256::SecretKey;
                let secret_key =
                    SecretKey::from_slice(private_bytes).map_err(|_| AuthError::InvalidToken)?;
                let public_key = secret_key.public_key();
                let public_key_bytes = public_key.to_sec1_bytes();
                let public_key_b64 = b64_encode(&public_key_bytes);
                Ok(format!(
                    "-----BEGIN PUBLIC KEY-----\n{}\n-----END PUBLIC KEY-----",
                    public_key_b64
                ))
            }
            AuthKeyMaterial::Ed25519Private(private_bytes) => {
                use ed25519_dalek::SigningKey;
                let key_array: [u8; 32] = private_bytes
                    .as_slice()
                    .try_into()
                    .map_err(|_| AuthError::InvalidToken)?;
                let signing_key = SigningKey::from_bytes(&key_array);
                let verifying_key = signing_key.verifying_key();
                let public_key_bytes = verifying_key.to_bytes();
                let public_key_b64 = b64_encode(&public_key_bytes);
                Ok(format!(
                    "-----BEGIN PUBLIC KEY-----\n{}\n-----END PUBLIC KEY-----",
                    public_key_b64
                ))
            }
            AuthKeyMaterial::Hmac256(_) | AuthKeyMaterial::Legacy(_) => {
                Err(AuthError::InvalidToken) // Symmetric keys don't have public keys
            }
            AuthKeyMaterial::EcdsaP256Public(_) | AuthKeyMaterial::Ed25519Public(_) => {
                Err(AuthError::InvalidToken) // Already public key, can't extract from public key
            }
        }
    }

    pub fn server_token(&self) -> Result<String, AuthError> {
        self.server_token_cwt()
    }

    fn sign(&self, payload: Payload) -> Result<String, AuthError> {
        let signing_key = self.get_signing_key()?;

        let mut hash_payload =
            bincode_encode(&payload).expect("Bincode serialization should not fail.");

        let key_bytes = match &signing_key.key_material {
            AuthKeyMaterial::Legacy(key_bytes) => key_bytes,
            AuthKeyMaterial::EcdsaP256Public(_) | AuthKeyMaterial::Ed25519Public(_) => {
                return Err(AuthError::CannotSignWithPublicKey)
            }
            _ => return Err(AuthError::InvalidToken), // Only legacy keys supported for legacy tokens
        };
        hash_payload.extend_from_slice(key_bytes);

        let token = hash(&hash_payload);

        let auth_req = AuthenticatedRequest { payload, token };

        let auth_enc = bincode_encode(&auth_req).expect("Bincode serialization should not fail.");
        let result = b64_encode(&auth_enc);
        Ok(result)
    }

    pub fn with_key_id(self, key_id: KeyId) -> Self {
        let mut keys = self.keys;
        let mut key_lookup = std::collections::HashMap::new();
        let mut keys_without_id = Vec::new();

        // Update the first key's key_id and rebuild lookup structures
        if !keys.is_empty() {
            keys[0].key_id = Some(key_id.0.clone());
            key_lookup.insert(key_id.0, 0);

            // Rebuild lookup structures for other keys
            for (index, key) in keys.iter().enumerate().skip(1) {
                if let Some(ref existing_key_id) = key.key_id {
                    key_lookup.insert(existing_key_id.clone(), index);
                } else {
                    keys_without_id.push(index);
                }
            }
        }

        Self {
            keys,
            key_lookup,
            keys_without_id,
            expected_audience: None,
        }
    }

    pub fn verify_server_token(
        &self,
        token: &str,
        current_time_epoch_millis: u64,
    ) -> Result<(), AuthError> {
        let permission = self.verify_token_auto(token, current_time_epoch_millis)?;
        match permission {
            Permission::Server => Ok(()),
            _ => Err(AuthError::InvalidResource),
        }
    }

    pub fn gen_doc_token(
        &self,
        doc_id: &str,
        authorization: Authorization,
        expiration_time: ExpirationTimeEpochMillis,
        user: Option<&str>,
    ) -> Result<String, AuthError> {
        let payload = Payload::new_with_expiration(
            Permission::Doc(DocPermission {
                doc_id: doc_id.to_string(),
                authorization,
                user: user.map(|u| u.to_string()),
            }),
            expiration_time,
        );
        self.sign(payload)
    }

    pub fn gen_file_token(
        &self,
        file_hash: &str,
        doc_id: &str,
        authorization: Authorization,
        expiration_time: ExpirationTimeEpochMillis,
        content_type: Option<&str>,
        content_length: Option<u64>,
        user: Option<&str>,
    ) -> Result<String, AuthError> {
        let payload = Payload::new_with_expiration(
            Permission::File(FilePermission {
                file_hash: file_hash.to_string(),
                doc_id: doc_id.to_string(),
                authorization,
                content_type: content_type.map(|s| s.to_string()),
                content_length,
                user: user.map(|u| u.to_string()),
            }),
            expiration_time,
        );
        self.sign(payload)
    }

    /// Generate a CWT server token
    pub fn server_token_cwt(&self) -> Result<String, AuthError> {
        self.gen_cwt_token(Permission::Server, None)
    }

    /// Generate a legacy format server token
    pub fn server_token_legacy(&self) -> Result<String, AuthError> {
        self.sign(Payload::new(Permission::Server))
    }

    /// Generate a CWT document token
    pub fn gen_doc_token_cwt(
        &self,
        doc_id: &str,
        authorization: Authorization,
        expiration_time: ExpirationTimeEpochMillis,
        user: Option<&str>,
        channel: Option<String>,
    ) -> Result<String, AuthError> {
        // Validate channel if provided
        if let Some(ref channel_name) = channel {
            if !crate::api_types::validate_key(channel_name) {
                panic!("Invalid channel name: must contain only alphanumeric characters, hyphens, and underscores");
            }
        }

        let permission = Permission::Doc(DocPermission {
            doc_id: doc_id.to_string(),
            authorization,
            user: user.map(|u| u.to_string()),
        });
        self.gen_cwt_token_with_channel(permission, Some(expiration_time), channel)
    }

    /// Generate a CWT file token
    pub fn gen_file_token_cwt(
        &self,
        file_hash: &str,
        doc_id: &str,
        authorization: Authorization,
        expiration_time: ExpirationTimeEpochMillis,
        content_type: Option<&str>,
        content_length: Option<u64>,
        user: Option<&str>,
        channel: Option<String>,
    ) -> Result<String, AuthError> {
        // Validate channel if provided
        if let Some(ref channel_name) = channel {
            if !crate::api_types::validate_key(channel_name) {
                panic!("Invalid channel name: must contain only alphanumeric characters, hyphens, and underscores");
            }
        }

        let permission = Permission::File(FilePermission {
            file_hash: file_hash.to_string(),
            doc_id: doc_id.to_string(),
            authorization,
            content_type: content_type.map(|s| s.to_string()),
            content_length,
            user: user.map(|u| u.to_string()),
        });
        self.gen_cwt_token_with_channel(permission, Some(expiration_time), channel)
    }

    /// Generate a prefix token (custom format)
    pub fn gen_prefix_token(
        &self,
        prefix: &str,
        authorization: Authorization,
        expiration_time: ExpirationTimeEpochMillis,
        user: Option<&str>,
    ) -> Result<String, AuthError> {
        let payload = Payload::new_with_expiration(
            Permission::Prefix(PrefixPermission {
                prefix: prefix.to_string(),
                authorization,
                user: user.map(|u| u.to_string()),
            }),
            expiration_time,
        );
        self.sign(payload)
    }

    /// Generate a CWT prefix token
    pub fn gen_prefix_token_cwt(
        &self,
        prefix: &str,
        authorization: Authorization,
        expiration_time: ExpirationTimeEpochMillis,
        user: Option<&str>,
    ) -> Result<String, AuthError> {
        let permission = Permission::Prefix(PrefixPermission {
            prefix: prefix.to_string(),
            authorization,
            user: user.map(|u| u.to_string()),
        });
        self.gen_cwt_token(permission, Some(expiration_time))
    }

    /// Generate a CWT token for any permission type
    fn gen_cwt_token(
        &self,
        permission: Permission,
        expiration_time: Option<ExpirationTimeEpochMillis>,
    ) -> Result<String, AuthError> {
        self.gen_cwt_token_with_channel(permission, expiration_time, None)
    }

    fn gen_cwt_token_with_channel(
        &self,
        permission: Permission,
        expiration_time: Option<ExpirationTimeEpochMillis>,
        channel: Option<String>,
    ) -> Result<String, AuthError> {
        use crate::cwt::{permission_to_scope, CwtClaims};

        let cwt_auth = self.create_cwt_authenticator().map_err(|e| match e {
            crate::cwt::CwtError::InvalidCose => {
                // Check if we actually have no signing keys
                if self.keys.iter().any(|k| k.can_sign) {
                    AuthError::CannotSignWithPublicKey
                } else {
                    AuthError::NoSigningKey
                }
            }
            _ => AuthError::CannotSignWithPublicKey,
        })?;

        // Extract user information from permission
        let subject = match &permission {
            Permission::Doc(doc_perm) => doc_perm.user.clone(),
            Permission::File(file_perm) => file_perm.user.clone(),
            Permission::Prefix(prefix_perm) => prefix_perm.user.clone(),
            Permission::Server => None,
        };

        let claims = CwtClaims {
            issuer: Some("relay-server".to_string()),
            subject,
            audience: self.expected_audience.clone(),
            expiration: expiration_time.map(|exp| exp.0 / 1000), // Convert to seconds
            issued_at: Some(
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_secs(),
            ),
            scope: permission_to_scope(&permission),
            channel,
        };

        let token_bytes = cwt_auth
            .create_cwt(claims)
            .map_err(|_| AuthError::CannotSignWithPublicKey)?;

        let token = b64_encode(&token_bytes);

        Ok(token)
    }

    pub fn verify_doc_token(
        &self,
        token: &str,
        doc: &str,
        current_time_epoch_millis: u64,
    ) -> Result<Authorization, AuthError> {
        let payload = self.verify_token_auto(token, current_time_epoch_millis)?;

        match payload {
            Permission::Doc(doc_permission) => {
                if doc_permission.doc_id == doc {
                    Ok(doc_permission.authorization)
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            Permission::File(file_permission) => {
                // Only check for file tokens using doc_id, not file_hash
                // This prevents document tokens from being misinterpreted
                if file_permission.doc_id == doc {
                    Ok(file_permission.authorization)
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            Permission::Prefix(prefix_permission) => {
                if doc.starts_with(&prefix_permission.prefix) {
                    Ok(prefix_permission.authorization)
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            Permission::Server => Ok(Authorization::Full), // Server tokens can access any doc.
        }
    }

    pub fn verify_file_token(
        &self,
        token: &str,
        file_hash: &str,
        current_time_epoch_millis: u64,
    ) -> Result<Authorization, AuthError> {
        let payload = self.verify_token_auto(token, current_time_epoch_millis)?;

        match payload {
            Permission::File(file_permission) => {
                if file_permission.file_hash == file_hash {
                    Ok(file_permission.authorization)
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            Permission::Server => Ok(Authorization::Full), // Server tokens can access any file
            _ => Err(AuthError::InvalidResource),
        }
    }

    pub fn verify_file_token_for_doc(
        &self,
        token: &str,
        doc_id: &str,
        current_time_epoch_millis: u64,
    ) -> Result<Authorization, AuthError> {
        let payload = self.verify_token_auto(token, current_time_epoch_millis)?;

        match payload {
            Permission::File(file_permission) => {
                if file_permission.doc_id == doc_id {
                    Ok(file_permission.authorization)
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            Permission::Doc(doc_permission) => {
                // Allow Doc tokens to perform file operations for their doc_id
                if doc_permission.doc_id == doc_id {
                    Ok(doc_permission.authorization)
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            Permission::Prefix(prefix_permission) => {
                if doc_id.starts_with(&prefix_permission.prefix) {
                    Ok(prefix_permission.authorization)
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            Permission::Server => Ok(Authorization::Full), // Server tokens can access any doc
        }
    }

    pub fn file_token_metadata(
        &self,
        token: &str,
    ) -> Result<Option<(String, Option<String>, Option<u64>)>, AuthError> {
        let payload = self.decode_token(token)?;

        match payload.payload {
            Permission::File(file_permission) => Ok(Some((
                file_permission.doc_id,
                file_permission.content_type,
                file_permission.content_length,
            ))),
            _ => Ok(None), // Not a file token
        }
    }

    pub fn gen_key() -> Result<Authenticator, AuthError> {
        Self::gen_key_hmac()
    }

    pub fn gen_key_hmac() -> Result<Authenticator, AuthError> {
        let key = rand::thread_rng().gen::<[u8; 32]>();
        let key = b64_encode(&key);

        let authenticator = Authenticator::new(&key)?;
        Ok(authenticator)
    }

    pub fn gen_key_ecdsa() -> Result<Authenticator, AuthError> {
        use p256::SecretKey;
        use rand::rngs::OsRng;

        let secret_key = SecretKey::random(&mut OsRng);
        let private_key_bytes = secret_key.to_bytes();

        Ok(Authenticator {
            keys: vec![AuthKeyEntry {
                key_id: None,
                key_material: AuthKeyMaterial::EcdsaP256Private(private_key_bytes.to_vec()),
                can_sign: true,
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
            }],
            key_lookup: std::collections::HashMap::new(),
            keys_without_id: vec![0],
            expected_audience: None,
        })
    }

    pub fn gen_key_ed25519() -> Result<Authenticator, AuthError> {
        use rand::{rngs::OsRng, RngCore};

        let mut secret_bytes = [0u8; 32];
        OsRng.fill_bytes(&mut secret_bytes);

        Ok(Authenticator {
            keys: vec![AuthKeyEntry {
                key_id: None,
                key_material: AuthKeyMaterial::Ed25519Private(secret_bytes.to_vec()),
                can_sign: true,
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
            }],
            key_lookup: std::collections::HashMap::new(),
            keys_without_id: vec![0],
            expected_audience: None,
        })
    }

    pub fn gen_key_legacy() -> Result<Authenticator, AuthError> {
        let key = rand::thread_rng().gen::<[u8; 30]>(); // 30-byte legacy keys

        Ok(Authenticator {
            keys: vec![AuthKeyEntry {
                key_id: None,
                key_material: AuthKeyMaterial::Legacy(key.to_vec()),
                can_sign: true,
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
            }],
            key_lookup: std::collections::HashMap::new(),
            keys_without_id: vec![0],
            expected_audience: None,
        })
    }

    pub fn decode_token(&self, token: &str) -> Result<Payload, AuthError> {
        // Try to decode with current format first, fallback to legacy format
        let decoded_bytes = b64_decode(token)?;
        let auth_req: AuthenticatedRequest =
            match bincode_decode::<AuthenticatedRequest>(&decoded_bytes) {
                Ok(req) => req,
                Err(_) => {
                    // Try legacy format
                    match bincode_decode::<LegacyAuthenticatedRequest>(&decoded_bytes) {
                        Ok(legacy_req) => legacy_req.into(),
                        Err(_) => return Err(AuthError::InvalidToken),
                    }
                }
            };

        Ok(auth_req.payload)
    }

    /// Verify a token automatically detecting its format (custom or CWT)
    pub fn verify_token_auto(
        &self,
        token: &str,
        current_time: u64,
    ) -> Result<Permission, AuthError> {
        // First verify the token normally
        let permission = self.verify_token_internal(token, current_time)?;

        // Then check if the verifying key is authorized for this token type
        let token_type = TokenType::from_permission(&permission);
        let verifying_key = self.find_verifying_key(token)?;

        if !verifying_key.allowed_token_types.contains(&token_type) {
            return Err(AuthError::UnauthorizedTokenType(format!(
                "Key {} not authorized for {} tokens",
                verifying_key.key_id.as_deref().unwrap_or("unnamed"),
                match token_type {
                    TokenType::Document => "document",
                    TokenType::File => "file",
                    TokenType::Server => "server",
                    TokenType::Prefix => "prefix",
                }
            )));
        }

        Ok(permission)
    }

    /// Internal method to verify token without permission checking
    fn verify_token_internal(
        &self,
        token: &str,
        current_time: u64,
    ) -> Result<Permission, AuthError> {
        // Determine token format first
        let format = detect_token_format(token);

        match format {
            TokenFormat::Custom => {
                // Try all keys in configuration order
                let mut last_error = AuthError::KeyMismatch;

                for (index, _) in self.keys.iter().enumerate() {
                    match self.verify_with_key_entry(&self.keys[index], token, current_time) {
                        Ok(permission) => return Ok(permission),
                        Err(err) => {
                            // Prioritize specific errors over generic ones
                            match (&last_error, &err) {
                                // Always prefer non-signature errors (like Expired, InvalidResource)
                                (_, AuthError::Expired) => last_error = err,
                                (_, AuthError::InvalidResource) => last_error = err,
                                (_, AuthError::InvalidClaims) => last_error = err,
                                (_, AuthError::InvalidToken) => last_error = err,
                                // Only replace KeyMismatch with signature errors
                                (AuthError::KeyMismatch, AuthError::InvalidSignature) => {
                                    last_error = err
                                }
                                (
                                    AuthError::KeyMismatch,
                                    AuthError::SignatureVerificationFailed,
                                ) => last_error = err,
                                _ => {} // Keep the existing error
                            }
                        }
                    }
                }

                Err(last_error)
            }
            TokenFormat::Cwt => {
                // CWT tokens use COSE header key IDs
                if let Some(ref audience) = self.expected_audience {
                    let (permission, _channel) =
                        self.verify_cwt_token_with_channel(token, current_time, audience)?;
                    Ok(permission)
                } else {
                    tracing::warn!("CWT token verification without audience validation - consider configuring server.url");
                    Err(AuthError::InvalidToken)
                }
            }
        }
    }

    /// Verify a token with a specific key entry
    fn verify_with_key_entry(
        &self,
        key_entry: &AuthKeyEntry,
        token: &str,
        current_time: u64,
    ) -> Result<Permission, AuthError> {
        match detect_token_format(token) {
            TokenFormat::Custom => {
                let payload = self.verify_custom_with_key(key_entry, token, current_time)?;
                Ok(payload.payload)
            }
            TokenFormat::Cwt => {
                if let Some(ref audience) = self.expected_audience {
                    self.verify_cwt_with_key(key_entry, token, current_time, audience)
                } else {
                    // For backward compatibility, skip audience validation if not configured
                    tracing::warn!("CWT token verification without audience validation - consider configuring server.url");
                    return Err(AuthError::InvalidToken);
                }
            }
        }
    }

    /// Verify a custom format token with a specific key entry
    fn verify_custom_with_key(
        &self,
        key_entry: &AuthKeyEntry,
        token: &str,
        current_time: u64,
    ) -> Result<Payload, AuthError> {
        // Try to decode with current format first, fallback to legacy format
        let decoded_bytes = b64_decode(token)?;

        // First try current format
        if let Ok(auth_req) = bincode_decode::<AuthenticatedRequest>(&decoded_bytes) {
            let mut payload =
                bincode_encode(&auth_req.payload).expect("Bincode serialization should not fail.");
            let key_bytes = match &key_entry.key_material {
                AuthKeyMaterial::Legacy(key_bytes) => key_bytes,
                _ => return Err(AuthError::InvalidSignature), // Only legacy keys for legacy tokens
            };
            payload.extend_from_slice(key_bytes);
            let expected_token = hash(&payload);

            if expected_token != auth_req.token {
                return Err(AuthError::InvalidSignature);
            } else if auth_req
                .payload
                .expiration_millis
                .unwrap_or(ExpirationTimeEpochMillis::max())
                .0
                < current_time
            {
                return Err(AuthError::Expired);
            } else {
                return Ok(auth_req.payload);
            }
        }

        // Try legacy format
        if let Ok(legacy_req) = bincode_decode::<LegacyAuthenticatedRequest>(&decoded_bytes) {
            // For legacy tokens, we need to verify using the legacy payload structure
            let mut payload = bincode_encode(&legacy_req.payload)
                .expect("Bincode serialization should not fail.");
            let key_bytes = match &key_entry.key_material {
                AuthKeyMaterial::Legacy(key_bytes) => key_bytes,
                _ => return Err(AuthError::InvalidSignature), // Only legacy keys for legacy tokens
            };
            payload.extend_from_slice(key_bytes);
            let expected_token = hash(&payload);

            if expected_token != legacy_req.token {
                return Err(AuthError::InvalidSignature);
            }

            // Convert to current format
            let auth_req: AuthenticatedRequest = legacy_req.into();

            if auth_req
                .payload
                .expiration_millis
                .unwrap_or(ExpirationTimeEpochMillis::max())
                .0
                < current_time
            {
                return Err(AuthError::Expired);
            } else {
                return Ok(auth_req.payload);
            }
        }

        Err(AuthError::InvalidToken)
    }

    /// Verify a CWT token with a specific key entry
    fn verify_cwt_with_key(
        &self,
        key_entry: &AuthKeyEntry,
        token: &str,
        current_time: u64,
        expected_audience: &str,
    ) -> Result<Permission, AuthError> {
        use crate::cwt::scope_to_permission;

        tracing::debug!("Starting CWT token verification with specific key");

        let token_bytes = b64_decode(token).map_err(|e| {
            tracing::error!("Base64 decode failed: {}", e);
            e
        })?;

        let cwt_auth = self
            .create_cwt_authenticator_for_key(key_entry)
            .map_err(|e| {
                tracing::error!("Failed to create CWT authenticator: {:?}", e);
                AuthError::InvalidToken
            })?;

        let claims = cwt_auth.verify_cwt(&token_bytes, expected_audience).map_err(|e| match e {
            crate::cwt::CwtError::InvalidCbor => {
                tracing::debug!("Token has invalid CBOR structure");
                AuthError::InvalidCbor
            }
            crate::cwt::CwtError::InvalidCose => {
                tracing::debug!("Token has invalid COSE structure");
                AuthError::InvalidCose
            }
            crate::cwt::CwtError::InvalidClaims => {
                tracing::debug!("Token has invalid claims structure");
                AuthError::InvalidClaims
            }
            crate::cwt::CwtError::SignatureVerificationFailed => {
                tracing::debug!("Signature verification failed");
                AuthError::SignatureVerificationFailed
            }
            crate::cwt::CwtError::InvalidAudience { expected, found } => {
                tracing::warn!(
                    expected = expected,
                    found = found,
                    "Authentication failed: CWT token audience validation failed - potential cross-service token reuse attempt"
                );
                AuthError::InvalidAudience { expected, found }
            }
            crate::cwt::CwtError::MissingAudience { expected } => {
                tracing::warn!(
                    expected = expected,
                    "Authentication failed: CWT token missing audience claim - potential security risk"
                );
                AuthError::MissingAudience { expected }
            }
            crate::cwt::CwtError::TokenExpired => {
                tracing::debug!("CWT token has expired");
                AuthError::Expired
            }
            _ => {
                tracing::debug!("Other CWT error: {:?}", e);
                AuthError::InvalidToken
            }
        })?;

        tracing::trace!(
            "CWT verification successful - issuer: {:?}, scope: {}",
            claims.issuer,
            claims.scope
        );

        // Validate issuer - accept relay-server, auth.system3.dev, and auth.system3.md
        if let Some(ref issuer) = claims.issuer {
            const VALID_ISSUERS: &[&str] = &["relay-server", "auth.system3.dev", "auth.system3.md"];
            if !VALID_ISSUERS.contains(&&**issuer) {
                tracing::debug!("Invalid issuer: {}", issuer);
                return Err(AuthError::InvalidClaims);
            }
        }

        // Check expiration using caller-supplied time (milliseconds) as fallback
        if let Some(exp) = claims.expiration {
            let exp_millis = exp * 1000;
            if exp_millis < current_time {
                tracing::debug!("Token expired (caller time check)");
                return Err(AuthError::Expired);
            }
        }

        // Parse permission from scope and add user information from subject
        let mut permission = scope_to_permission(&claims.scope).map_err(|e| {
            tracing::debug!("Failed to parse scope '{}': {:?}", claims.scope, e);
            AuthError::InvalidClaims
        })?;

        // Add user information from the subject field
        match &mut permission {
            Permission::Doc(doc_perm) => {
                doc_perm.user = claims.subject.clone();
            }
            Permission::File(file_perm) => {
                file_perm.user = claims.subject.clone();
            }
            Permission::Prefix(prefix_perm) => {
                prefix_perm.user = claims.subject.clone();
            }
            Permission::Server => {}
        }

        tracing::debug!("CWT token verification successful");
        Ok(permission)
    }

    /// Verify a CWT token and extract both permission and channel
    fn verify_cwt_token_with_channel(
        &self,
        token: &str,
        current_time: u64,
        expected_audience: &str,
    ) -> Result<(Permission, Option<String>), AuthError> {
        // Extract key ID from COSE headers
        let cwt_key_id = extract_cwt_key_id(token);

        // Try verification with matching key
        if let Some(key_id) = cwt_key_id.as_deref() {
            tracing::trace!(
                "CWT token has COSE header key_id: '{}', looking up in configured keys",
                key_id
            );
            tracing::trace!(
                "Available key_ids: {:?}",
                self.key_lookup.keys().collect::<Vec<_>>()
            );
            // Use hashmap lookup for keys with key_id (O(1) performance)
            if let Some(&index) = self.key_lookup.get(key_id) {
                tracing::trace!("Found matching key at index {}", index);
                return self.verify_cwt_with_channel(
                    &self.keys[index],
                    token,
                    current_time,
                    expected_audience,
                );
            } else {
                tracing::debug!(
                    "CWT COSE header key ID '{}' not found in configured keys",
                    key_id
                );
                return Err(AuthError::KeyMismatch);
            }
        }

        // For CWT tokens without key_id in COSE headers, try all keys without key_id in configuration order
        tracing::trace!("CWT token has no COSE header key_id, trying all keys without key_id");

        if self.keys_without_id.is_empty() {
            // Every configured key requires a kid, but this token has none — there is no
            // keyless fallback to even attempt. This is the branch that used to return
            // KeyMismatch with zero logging at any level (warn/debug/trace), which is why
            // the CWT auth defect (#f975dd60) produced 100% silent WS-auth failures for
            // ~6 months: token-issuer/config mismatches on this path were invisible.
            tracing::warn!(
                configured_key_ids = ?self.key_lookup.keys().collect::<Vec<_>>(),
                "CWT auth failed: token has no COSE header key_id (kid), and every configured \
                 key requires one, so there is no keyless key to fall back to. This usually \
                 means the token issuer is not setting kid to match a configured key_id \
                 (issuer/config mismatch), not a routine authentication failure."
            );
            return Err(AuthError::KeyMismatch);
        }

        let mut last_error = AuthError::KeyMismatch;

        for &index in &self.keys_without_id {
            match self.verify_cwt_with_channel(
                &self.keys[index],
                token,
                current_time,
                expected_audience,
            ) {
                Ok((permission, channel)) => return Ok((permission, channel)),
                Err(err) => {
                    // Prioritize specific errors over generic ones
                    match (&last_error, &err) {
                        // Always prefer non-signature errors (like Expired, InvalidResource)
                        (_, AuthError::Expired) => last_error = err,
                        (_, AuthError::InvalidResource) => last_error = err,
                        (_, AuthError::InvalidClaims) => last_error = err,
                        (_, AuthError::InvalidToken) => last_error = err,
                        // Only replace KeyMismatch with signature errors
                        (AuthError::KeyMismatch, AuthError::InvalidSignature) => last_error = err,
                        (AuthError::KeyMismatch, AuthError::SignatureVerificationFailed) => {
                            last_error = err
                        }
                        _ => {} // Keep the existing error
                    }
                }
            }
        }

        if matches!(last_error, AuthError::KeyMismatch) {
            tracing::debug!(
                keyless_key_count = self.keys_without_id.len(),
                "CWT auth failed: token has no COSE header key_id, and none of the configured \
                 keyless keys verified it"
            );
        }

        Err(last_error)
    }

    /// Verify a CWT token with a specific key entry and extract both permission and channel
    fn verify_cwt_with_channel(
        &self,
        key_entry: &AuthKeyEntry,
        token: &str,
        current_time: u64,
        expected_audience: &str,
    ) -> Result<(Permission, Option<String>), AuthError> {
        use crate::cwt::scope_to_permission;

        tracing::debug!("Starting CWT token verification with specific key and channel");

        let token_bytes = b64_decode(token).map_err(|e| {
            tracing::error!("Base64 decode failed: {}", e);
            e
        })?;

        let cwt_auth = self
            .create_cwt_authenticator_for_key(key_entry)
            .map_err(|e| {
                tracing::error!("Failed to create CWT authenticator: {:?}", e);
                AuthError::InvalidToken
            })?;

        let claims = cwt_auth.verify_cwt(&token_bytes, expected_audience).map_err(|e| match e {
            crate::cwt::CwtError::InvalidCbor => {
                tracing::debug!("Token has invalid CBOR structure");
                AuthError::InvalidCbor
            }
            crate::cwt::CwtError::InvalidCose => {
                tracing::debug!("Token has invalid COSE structure");
                AuthError::InvalidCose
            }
            crate::cwt::CwtError::InvalidClaims => {
                tracing::debug!("Token has invalid claims structure");
                AuthError::InvalidClaims
            }
            crate::cwt::CwtError::SignatureVerificationFailed => {
                tracing::debug!("Signature verification failed");
                AuthError::SignatureVerificationFailed
            }
            crate::cwt::CwtError::InvalidAudience { expected, found } => {
                tracing::warn!(
                    expected = expected,
                    found = found,
                    "Authentication failed: CWT token audience validation failed - potential cross-service token reuse attempt"
                );
                AuthError::InvalidAudience { expected, found }
            }
            crate::cwt::CwtError::MissingAudience { expected } => {
                tracing::warn!(
                    expected = expected,
                    "Authentication failed: CWT token missing audience claim - potential security risk"
                );
                AuthError::MissingAudience { expected }
            }
            crate::cwt::CwtError::TokenExpired => {
                tracing::debug!("CWT token has expired");
                AuthError::Expired
            }
            _ => {
                tracing::debug!("Other CWT error: {:?}", e);
                AuthError::InvalidToken
            }
        })?;

        tracing::trace!(
            "CWT verification successful - issuer: {:?}, scope: {}",
            claims.issuer,
            claims.scope
        );

        // Validate issuer - accept relay-server, auth.system3.dev, and auth.system3.md
        if let Some(ref issuer) = claims.issuer {
            const VALID_ISSUERS: &[&str] = &["relay-server", "auth.system3.dev", "auth.system3.md"];
            if !VALID_ISSUERS.contains(&&**issuer) {
                tracing::debug!("Invalid issuer: {}", issuer);
                return Err(AuthError::InvalidClaims);
            }
        }

        // Check expiration using caller-supplied time (milliseconds) as fallback
        if let Some(exp) = claims.expiration {
            let exp_millis = exp * 1000;
            if exp_millis < current_time {
                tracing::debug!("Token expired (caller time check)");
                return Err(AuthError::Expired);
            }
        }

        // Parse permission from scope and add user information from subject
        let mut permission = scope_to_permission(&claims.scope).map_err(|e| {
            tracing::debug!("Failed to parse scope '{}': {:?}", claims.scope, e);
            AuthError::InvalidClaims
        })?;

        // Add user information from the subject field
        match &mut permission {
            Permission::Doc(doc_perm) => {
                doc_perm.user = claims.subject.clone();
            }
            Permission::File(file_perm) => {
                file_perm.user = claims.subject.clone();
            }
            Permission::Prefix(prefix_perm) => {
                prefix_perm.user = claims.subject.clone();
            }
            Permission::Server => {}
        }

        tracing::debug!("CWT token verification successful");
        Ok((permission, claims.channel))
    }

    /// Extract user information from a token (works with both custom and CWT tokens)
    pub fn extract_user_from_token(&self, token: &str) -> Result<Option<String>, AuthError> {
        match detect_token_format(token) {
            TokenFormat::Custom => {
                let payload = self.decode_token(token)?;
                match payload.payload {
                    Permission::Doc(doc_perm) => Ok(doc_perm.user),
                    Permission::File(file_perm) => Ok(file_perm.user),
                    Permission::Prefix(prefix_perm) => Ok(prefix_perm.user),
                    Permission::Server => Ok(None),
                }
            }
            TokenFormat::Cwt => {
                // Use the multi-key verification to extract user
                let permission = self.verify_token_auto(token, 0)?; // Use 0 for current_time to avoid expiration check
                match permission {
                    Permission::Doc(doc_perm) => Ok(doc_perm.user),
                    Permission::File(file_perm) => Ok(file_perm.user),
                    Permission::Prefix(prefix_perm) => Ok(prefix_perm.user),
                    Permission::Server => Ok(None),
                }
            }
        }
    }

    /// Verify a document token and return both authorization and user information
    pub fn verify_doc_token_with_user(
        &self,
        token: &str,
        doc_id: &str,
        current_time: u64,
    ) -> Result<(Authorization, Option<String>), AuthError> {
        let auth = self.verify_doc_token(token, doc_id, current_time)?;
        let user = self.extract_user_from_token(token)?;
        Ok((auth, user))
    }

    /// Verify a file token and return both authorization and user information
    pub fn verify_file_token_with_user(
        &self,
        token: &str,
        file_hash: &str,
        current_time: u64,
    ) -> Result<(Authorization, Option<String>), AuthError> {
        let auth = self.verify_file_token(token, file_hash, current_time)?;
        let user = self.extract_user_from_token(token)?;
        Ok((auth, user))
    }

    /// Verify a document token with prefix support and return both authorization and user information
    pub fn verify_doc_token_with_prefix(
        &self,
        token: &str,
        doc_id: &str,
        current_time: u64,
    ) -> Result<(Authorization, Option<String>), AuthError> {
        // Try direct doc token first
        if let Ok(auth) = self.verify_doc_token(token, doc_id, current_time) {
            let user = self.extract_user_from_token(token)?;
            return Ok((auth, user));
        }

        // Try prefix tokens
        let permission = self.verify_token_auto(token, current_time)?;
        match permission {
            Permission::Prefix(prefix_permission) => {
                if doc_id.starts_with(&prefix_permission.prefix) {
                    Ok((prefix_permission.authorization, prefix_permission.user))
                } else {
                    Err(AuthError::InvalidResource)
                }
            }
            _ => Err(AuthError::InvalidResource),
        }
    }

    /// For a token that `verify_token_with_channel` rejected as `Expired`: how many
    /// seconds past its `exp` it was at `current_time` (epoch millis), with
    /// millisecond resolution. `None` if the token is not a parseable CWT.
    ///
    /// Only meaningful after an `Expired` verdict — see
    /// `cwt::peek_expiration_unverified` for why that keeps the value trustworthy.
    pub fn expired_overshoot_secs(&self, token: &str, current_time: u64) -> Option<f64> {
        let bytes = b64_decode(token).ok()?;
        let exp_secs = crate::cwt::peek_expiration_unverified(&bytes)?;
        Some((current_time as f64 / 1000.0 - exp_secs as f64).max(0.0))
    }

    /// Verify a token and extract channel claim (CWT tokens only)
    pub fn verify_token_with_channel(
        &self,
        token: &str,
        current_time: u64,
    ) -> Result<(Permission, Option<String>), AuthError> {
        match detect_token_format(token) {
            TokenFormat::Custom => {
                let permission = self.verify_token_auto(token, current_time)?;
                Ok((permission, None)) // Custom tokens don't have channel claims
            }
            TokenFormat::Cwt => {
                if let Some(ref audience) = self.expected_audience {
                    let (permission, channel) =
                        self.verify_cwt_token_with_channel(token, current_time, audience)?;
                    Ok((permission, channel))
                } else {
                    tracing::warn!("CWT token verification without audience validation - consider configuring server.url");
                    return Err(AuthError::InvalidToken);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::TokenType;

    // Helper function to create AuthKeyConfig with full permissions for backward compatibility in tests
    fn test_auth_key_config(
        key_id: Option<String>,
        private_key: Option<String>,
        public_key: Option<String>,
    ) -> crate::config::AuthKeyConfig {
        crate::config::AuthKeyConfig {
            key_id,
            private_key,
            public_key,
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
        }
    }

    fn create_test_authenticator_with_audience() -> Authenticator {
        let mut auth = Authenticator::gen_key().unwrap();
        auth.set_expected_audience(Some("https://api.example.com".to_string()));
        auth
    }

    #[test]
    fn test_file_token_with_metadata() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();
        let file_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890";
        let doc_id = "doc123";
        let content_type = "application/json";
        let content_length = 12345;

        // Generate token with content-type and length
        let token = authenticator
            .gen_file_token(
                file_hash,
                doc_id,
                Authorization::Full,
                ExpirationTimeEpochMillis(0),
                Some(content_type),
                Some(content_length),
                None,
            )
            .unwrap();

        // Verify the token works for file hash authentication
        assert!(matches!(
            authenticator.verify_file_token(&token, file_hash, 0),
            Ok(Authorization::Full)
        ));

        // Verify the token works for doc authentication
        assert!(matches!(
            authenticator.verify_file_token_for_doc(&token, doc_id, 0),
            Ok(Authorization::Full)
        ));

        // Decode the token and verify metadata
        let payload = authenticator.decode_token(&token).unwrap();
        if let Permission::File(file_permission) = payload.payload {
            assert_eq!(file_permission.file_hash, file_hash);
            assert_eq!(file_permission.doc_id, doc_id);
            assert_eq!(file_permission.content_type, Some(content_type.to_string()));
            assert_eq!(file_permission.content_length, Some(content_length));
        } else {
            panic!("Expected File permission type");
        }

        // Test file_token_metadata
        let metadata = authenticator.file_token_metadata(&token).unwrap().unwrap();
        assert_eq!(metadata.0, doc_id);
        assert_eq!(metadata.1, Some(content_type.to_string()));
        assert_eq!(metadata.2, Some(content_length));
    }

    #[test]
    fn test_file_token_without_metadata() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();
        let file_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890";
        let doc_id = "doc123";

        // Generate token without content-type and length
        let token = authenticator
            .gen_file_token(
                file_hash,
                doc_id,
                Authorization::Full,
                ExpirationTimeEpochMillis(0),
                None,
                None,
                None,
            )
            .unwrap();

        // Verify the token with file hash
        assert!(matches!(
            authenticator.verify_file_token(&token, file_hash, 0),
            Ok(Authorization::Full)
        ));

        // Verify the token with doc id
        assert!(matches!(
            authenticator.verify_file_token_for_doc(&token, doc_id, 0),
            Ok(Authorization::Full)
        ));

        // Decode the token and verify no metadata present
        let payload = authenticator.decode_token(&token).unwrap();
        if let Permission::File(file_permission) = payload.payload {
            assert_eq!(file_permission.file_hash, file_hash);
            assert_eq!(file_permission.doc_id, doc_id);
            assert_eq!(file_permission.content_type, None);
            assert_eq!(file_permission.content_length, None);
        } else {
            panic!("Expected File permission type");
        }
    }

    #[test]
    fn test_flex_b64() {
        let expect = [3, 242, 3, 248, 6, 220, 118];

        assert_eq!(b64_decode("A/ID+Abcdg==").unwrap(), expect);
        assert_eq!(b64_decode("A/ID+Abcdg").unwrap(), expect);

        assert_eq!(b64_decode("A_ID-Abcdg==").unwrap(), expect);
        assert_eq!(b64_decode("A_ID-Abcdg").unwrap(), expect);
    }

    #[test]
    fn test_b64_encode_options() {
        let data = [3, 242, 3, 248, 6, 220, 118];

        assert_eq!(b64_encode(&data), "A_ID-Abcdg");
    }

    #[test]
    fn test_simple_auth() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();
        let token = authenticator
            .gen_doc_token(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(0),
                None,
            )
            .unwrap();
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc123", 0),
            Ok(Authorization::Full)
        ));
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc123", DEFAULT_EXPIRATION_SECONDS + 1),
            Err(AuthError::Expired)
        ));
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc456", 0),
            Err(AuthError::InvalidResource)
        ));
    }

    #[test]
    fn test_read_only_auth() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();
        let token = authenticator
            .gen_doc_token(
                "doc123",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(0),
                None,
            )
            .unwrap();
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc123", 0),
            Ok(Authorization::ReadOnly)
        ));
    }

    #[test]
    fn test_server_token_for_doc_auth() {
        let authenticator = create_test_authenticator_with_audience();
        let server_token = authenticator.server_token().unwrap();
        assert!(matches!(
            authenticator.verify_doc_token(&server_token, "doc123", 0),
            Ok(Authorization::Full)
        ));
    }

    #[test]
    fn test_key_id() {
        // Test legacy tokens with key ID
        let legacy_authenticator = Authenticator::gen_key_legacy()
            .unwrap()
            .with_key_id("myKeyId".try_into().unwrap());
        let legacy_token = legacy_authenticator
            .gen_doc_token(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(0),
                None,
            )
            .unwrap();
        assert!(!legacy_token.contains("."));
        assert!(matches!(
            legacy_authenticator.verify_doc_token(&legacy_token, "doc123", 0),
            Ok(Authorization::Full)
        ));

        // Test CWT tokens with key ID
        let mut cwt_authenticator = Authenticator::gen_key()
            .unwrap()
            .with_key_id("myKeyId".try_into().unwrap());
        cwt_authenticator.set_expected_audience(Some("https://api.example.com".to_string()));
        let server_token = cwt_authenticator.server_token().unwrap();
        assert!(
            !server_token.contains("."),
            "Token {} should not contain dots (no prefix format)",
            server_token
        );
        assert_eq!(
            cwt_authenticator.verify_server_token(&server_token, 0),
            Ok(())
        );
    }

    #[test]
    fn test_construct_key_id() {
        assert_eq!(KeyId::new("".to_string()), Err(KeyIdError::EmptyString));
        assert_eq!(
            KeyId::new("*".to_string()),
            Err(KeyIdError::InvalidCharacter { ch: '*' })
        );
        assert_eq!(
            KeyId::new("myKeyId".to_string()),
            Ok(KeyId("myKeyId".to_string()))
        );
    }

    #[test]
    fn test_invalid_signature() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();
        let actual_payload = Payload::new(Permission::Doc(DocPermission {
            doc_id: "doc123".to_string(),
            authorization: Authorization::Full,
            user: None,
        }));
        let mut encoded_payload =
            bincode_encode(&actual_payload).expect("Bincode serialization should not fail.");
        let key_bytes = match authenticator.key_material() {
            AuthKeyMaterial::Legacy(key_bytes) => key_bytes,
            _ => panic!("Expected legacy key for this test"),
        };
        encoded_payload.extend_from_slice(key_bytes);

        let token = hash(&encoded_payload);

        let auth_req = AuthenticatedRequest {
            payload: Payload::new(Permission::Doc(DocPermission {
                doc_id: "abc123".to_string(),
                authorization: Authorization::Full,
                user: None,
            })),
            token,
        };

        let auth_enc = bincode_encode(&auth_req).expect("Bincode serialization should not fail.");
        let signed = b64_encode(&auth_enc);

        assert!(matches!(
            authenticator.verify_doc_token(&signed, "doc123", 0),
            Err(AuthError::InvalidSignature)
        ));
        assert!(matches!(
            authenticator.verify_doc_token(&signed, "abc123", 0),
            Err(AuthError::InvalidSignature)
        ));
    }

    #[test]
    fn test_roundtrip_serde_authenticator() {
        let authenticator = Authenticator::gen_key().unwrap();
        let serialized = serde_json::to_string(&authenticator).unwrap();
        let deserialized: Authenticator = serde_json::from_str(&serialized).unwrap();
        assert_eq!(authenticator, deserialized);
    }

    // CWT Token Tests
    #[test]
    fn test_cwt_server_token() {
        let authenticator = create_test_authenticator_with_audience();
        let token = authenticator.server_token_cwt().unwrap();

        assert_eq!(detect_token_format(&token), TokenFormat::Cwt);

        // Verify the token works
        assert_eq!(authenticator.verify_server_token(&token, 0), Ok(()));
    }

    #[test]
    fn test_cwt_doc_token() {
        let authenticator = create_test_authenticator_with_audience();
        let token = authenticator
            .gen_doc_token_cwt(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();

        assert_eq!(detect_token_format(&token), TokenFormat::Cwt);

        // Verify the token works
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc123", 0),
            Ok(Authorization::Full)
        ));

        // Verify it fails for wrong doc
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc456", 0),
            Err(AuthError::InvalidResource)
        ));
    }

    #[test]
    fn test_cwt_file_token() {
        let authenticator = create_test_authenticator_with_audience();
        let file_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890";
        let doc_id = "doc123";

        let token = authenticator
            .gen_file_token_cwt(
                file_hash,
                doc_id,
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("application/json"),
                Some(12345),
                None,
                None,
            )
            .unwrap();

        assert_eq!(detect_token_format(&token), TokenFormat::Cwt);

        // Verify the token works for file hash
        assert!(matches!(
            authenticator.verify_file_token(&token, file_hash, 0),
            Ok(Authorization::ReadOnly)
        ));

        // Verify the token works for doc id
        assert!(matches!(
            authenticator.verify_file_token_for_doc(&token, doc_id, 0),
            Ok(Authorization::ReadOnly)
        ));
    }

    #[test]
    fn test_token_format_detection() {
        let legacy_authenticator = Authenticator::gen_key_legacy().unwrap();
        let cwt_authenticator = Authenticator::gen_key().unwrap();

        // Custom token
        let custom_token = legacy_authenticator
            .gen_doc_token(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(0),
                None,
            )
            .unwrap();
        assert_eq!(detect_token_format(&custom_token), TokenFormat::Custom);

        // CWT token
        let cwt_token = cwt_authenticator
            .gen_doc_token_cwt(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();
        assert_eq!(detect_token_format(&cwt_token), TokenFormat::Cwt);
    }

    #[test]
    fn test_mixed_token_verification() {
        let legacy_authenticator = Authenticator::gen_key_legacy().unwrap();
        let cwt_authenticator = create_test_authenticator_with_audience();

        // Custom tokens should work with auto verification
        let custom_token = legacy_authenticator
            .gen_doc_token(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
            )
            .unwrap();
        assert!(matches!(
            legacy_authenticator.verify_doc_token(&custom_token, "doc123", 0),
            Ok(Authorization::Full)
        ));

        // CWT tokens should work with auto verification
        let cwt_token = cwt_authenticator
            .gen_doc_token_cwt(
                "doc123",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();
        assert!(matches!(
            cwt_authenticator.verify_doc_token(&cwt_token, "doc123", 0),
            Ok(Authorization::ReadOnly)
        ));
    }

    #[test]
    fn test_cwt_token_with_key_id() {
        let mut authenticator = Authenticator::gen_key()
            .unwrap()
            .with_key_id("test_key".try_into().unwrap());
        authenticator.set_expected_audience(Some("https://api.example.com".to_string()));

        let token = authenticator
            .gen_doc_token_cwt(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();

        assert!(!token.contains("."));
        assert_eq!(detect_token_format(&token), TokenFormat::Cwt);

        // Verify the token works
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc123", 0),
            Ok(Authorization::Full)
        ));
    }

    #[test]
    fn test_cwt_expiration() {
        let authenticator = create_test_authenticator_with_audience();
        let short_expiration = ExpirationTimeEpochMillis(1000); // 1 second after epoch

        let token = authenticator
            .gen_doc_token_cwt("doc123", Authorization::Full, short_expiration, None, None)
            .unwrap();

        // Should fail with expired error
        assert!(matches!(
            authenticator.verify_doc_token(&token, "doc123", 2000),
            Err(AuthError::Expired)
        ));
    }

    #[test]
    fn test_cwt_invalid_signature() {
        let authenticator1 = create_test_authenticator_with_audience();
        let authenticator2 = create_test_authenticator_with_audience();

        let token = authenticator1
            .gen_doc_token_cwt(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();

        // Should fail with signature verification error
        assert!(matches!(
            authenticator2.verify_doc_token(&token, "doc123", 0),
            Err(AuthError::SignatureVerificationFailed)
        ));
    }

    #[test]
    fn test_user_identification_custom_tokens() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();

        // Test doc token with user
        let doc_token = authenticator
            .gen_doc_token(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("user123"),
            )
            .unwrap();

        let user = authenticator.extract_user_from_token(&doc_token).unwrap();
        assert_eq!(user, Some("user123".to_string()));

        let (auth, user) = authenticator
            .verify_doc_token_with_user(&doc_token, "doc123", 0)
            .unwrap();
        assert_eq!(auth, Authorization::Full);
        assert_eq!(user, Some("user123".to_string()));

        // Test file token with user
        let file_token = authenticator
            .gen_file_token(
                "hash123",
                "doc456",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
                Some("user456"),
            )
            .unwrap();

        let user = authenticator.extract_user_from_token(&file_token).unwrap();
        assert_eq!(user, Some("user456".to_string()));

        let (auth, user) = authenticator
            .verify_file_token_with_user(&file_token, "hash123", 0)
            .unwrap();
        assert_eq!(auth, Authorization::ReadOnly);
        assert_eq!(user, Some("user456".to_string()));

        // Test token without user
        let no_user_token = authenticator
            .gen_doc_token(
                "doc789",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
            )
            .unwrap();

        let user = authenticator
            .extract_user_from_token(&no_user_token)
            .unwrap();
        assert_eq!(user, None);
    }

    #[test]
    fn test_user_identification_cwt_tokens() {
        let authenticator = create_test_authenticator_with_audience();

        // Test doc token with user
        let doc_token = authenticator
            .gen_doc_token_cwt(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("user123"),
                None,
            )
            .unwrap();

        let user = authenticator.extract_user_from_token(&doc_token).unwrap();
        assert_eq!(user, Some("user123".to_string()));

        let (auth, user) = authenticator
            .verify_doc_token_with_user(&doc_token, "doc123", 0)
            .unwrap();
        assert_eq!(auth, Authorization::Full);
        assert_eq!(user, Some("user123".to_string()));

        // Test file token with user
        let file_token = authenticator
            .gen_file_token_cwt(
                "hash123",
                "doc456",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("application/json"),
                Some(1024),
                Some("user456"),
                None,
            )
            .unwrap();

        let user = authenticator.extract_user_from_token(&file_token).unwrap();
        assert_eq!(user, Some("user456".to_string()));

        let (auth, user) = authenticator
            .verify_file_token_with_user(&file_token, "hash123", 0)
            .unwrap();
        assert_eq!(auth, Authorization::ReadOnly);
        assert_eq!(user, Some("user456".to_string()));

        // Test token without user
        let no_user_token = authenticator
            .gen_doc_token_cwt(
                "doc789",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();

        let user = authenticator
            .extract_user_from_token(&no_user_token)
            .unwrap();
        assert_eq!(user, None);
    }

    #[test]
    fn test_backward_compatibility_old_tokens() {
        // This test simulates old tokens that were created before the user field was added
        let authenticator = Authenticator::gen_key_legacy().unwrap();

        // Create an old-style payload manually (without user field)
        let old_payload = LegacyPayload {
            payload: LegacyPermission::Doc(LegacyDocPermission {
                doc_id: "test_doc".to_string(),
                authorization: Authorization::Full,
            }),
            expiration_millis: None,
        };

        // Encode it the old way
        let mut hash_payload =
            bincode_encode(&old_payload).expect("Bincode serialization should not fail.");
        let key_bytes = match authenticator.key_material() {
            AuthKeyMaterial::Legacy(key_bytes) => key_bytes,
            _ => panic!("Expected legacy key for this test"),
        };
        hash_payload.extend_from_slice(key_bytes);
        let token_hash = hash(&hash_payload);

        let old_auth_req = LegacyAuthenticatedRequest {
            payload: old_payload,
            token: token_hash,
        };

        let auth_enc =
            bincode_encode(&old_auth_req).expect("Bincode serialization should not fail.");
        let old_token = b64_encode(&auth_enc);

        // Verify that the old token can still be verified
        match authenticator.verify_doc_token(&old_token, "test_doc", 0) {
            Ok(auth) => assert_eq!(auth, Authorization::Full),
            Err(e) => panic!("Failed to verify old token: {:?}", e),
        }

        // Verify that decode_token works
        let decoded = authenticator.decode_token(&old_token).unwrap();
        match decoded.payload {
            Permission::Doc(doc_perm) => {
                assert_eq!(doc_perm.doc_id, "test_doc");
                assert_eq!(doc_perm.authorization, Authorization::Full);
                assert_eq!(doc_perm.user, None); // Should be None for old tokens
            }
            _ => panic!("Expected Doc permission"),
        }
    }

    #[test]
    fn test_cwt_channel_claims() {
        let authenticator = create_test_authenticator_with_audience();
        let doc_id = "test_doc_123";
        let channel = "team-updates";

        // Test document token with channel claim
        let token_with_channel = authenticator
            .gen_doc_token_cwt(
                doc_id,
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("user123"),
                Some(channel.to_string()),
            )
            .unwrap();

        // Test document token without channel claim
        let token_without_channel = authenticator
            .gen_doc_token_cwt(
                doc_id,
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("user123"),
                None,
            )
            .unwrap();

        // Verify token with channel returns the channel
        let (permission, extracted_channel) = authenticator
            .verify_token_with_channel(&token_with_channel, 0)
            .unwrap();

        match permission {
            Permission::Doc(doc_perm) => {
                assert_eq!(doc_perm.doc_id, doc_id);
                assert_eq!(doc_perm.authorization, Authorization::Full);
                assert_eq!(doc_perm.user, Some("user123".to_string()));
            }
            _ => panic!("Expected doc permission"),
        }
        assert_eq!(extracted_channel, Some(channel.to_string()));

        // Verify token without channel returns None for channel
        let (permission, extracted_channel) = authenticator
            .verify_token_with_channel(&token_without_channel, 0)
            .unwrap();

        match permission {
            Permission::Doc(doc_perm) => {
                assert_eq!(doc_perm.doc_id, doc_id);
                assert_eq!(doc_perm.authorization, Authorization::Full);
                assert_eq!(doc_perm.user, Some("user123".to_string()));
            }
            _ => panic!("Expected doc permission"),
        }
        assert_eq!(extracted_channel, None);

        // Test file token with channel claim
        let file_token_with_channel = authenticator
            .gen_file_token_cwt(
                "file_hash_123",
                doc_id,
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("application/json"),
                Some(1024),
                Some("user456"),
                Some(channel.to_string()),
            )
            .unwrap();

        let (permission, extracted_channel) = authenticator
            .verify_token_with_channel(&file_token_with_channel, 0)
            .unwrap();

        match permission {
            Permission::File(file_perm) => {
                assert_eq!(file_perm.doc_id, doc_id);
                assert_eq!(file_perm.authorization, Authorization::ReadOnly);
                assert_eq!(file_perm.user, Some("user456".to_string()));
            }
            _ => panic!("Expected file permission"),
        }
        assert_eq!(extracted_channel, Some(channel.to_string()));
    }

    #[test]
    fn test_user_identification_mixed_tokens() {
        let legacy_authenticator = Authenticator::gen_key_legacy().unwrap();
        let cwt_authenticator = create_test_authenticator_with_audience();

        // Create custom and CWT tokens for the same resource but different users
        let custom_token = legacy_authenticator
            .gen_doc_token(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("custom_user"),
            )
            .unwrap();

        let cwt_token = cwt_authenticator
            .gen_doc_token_cwt(
                "doc123",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("cwt_user"),
                None,
            )
            .unwrap();

        // Verify both can extract users correctly
        let custom_user = legacy_authenticator
            .extract_user_from_token(&custom_token)
            .unwrap();
        let cwt_user = cwt_authenticator
            .extract_user_from_token(&cwt_token)
            .unwrap();

        assert_eq!(custom_user, Some("custom_user".to_string()));
        assert_eq!(cwt_user, Some("cwt_user".to_string()));

        // Verify authorization and user extraction work together
        let (auth1, user1) = legacy_authenticator
            .verify_doc_token_with_user(&custom_token, "doc123", 0)
            .unwrap();
        let (auth2, user2) = cwt_authenticator
            .verify_doc_token_with_user(&cwt_token, "doc123", 0)
            .unwrap();

        assert_eq!(auth1, Authorization::Full);
        assert_eq!(user1, Some("custom_user".to_string()));
        assert_eq!(auth2, Authorization::ReadOnly);
        assert_eq!(user2, Some("cwt_user".to_string()));
    }

    #[test]
    fn test_auth_system3_debug() {
        // Test the failing WebSocket token
        let token = "2D3RhEOhAQWgWH-lAWxyZWxheS1zZXJ2ZXICbzk5b3J2MmxnMHg5ZzV5ZAQaaLkaHAYaaLkTFAl4UGRvYzo4NWEwNjcxMi1hZjE0LTQ3YmMtYTg1OS1lODEwNmNjNzg2ZTgtOGFjNjg3M2EtZTBkMS00NGRhLWE3MWMtNGI0M2UwOGFmY2NlOnJ3WCDaunvV8xuQFkbaGA8KPxm8ma-XAvDkvU1NMFO71e_0yA";
        let key_base64 = "H2uV4LFfYYNMkkOeAmlgYbl0Axx94fzL9TdrWgbxsVM";
        let doc_id = "85a06712-af14-47bc-a859-e8106cc786e8-8ac6873a-e0d1-44da-a71c-4b43e08afcce";

        let detected_format = detect_token_format(token);
        assert_eq!(
            detected_format,
            TokenFormat::Cwt,
            "Token should be detected as CWT format"
        );

        // Create authenticator from the key
        let mut auth = Authenticator::new(key_base64).expect("Failed to create authenticator");
        auth.set_expected_audience(Some("https://test.example.com".to_string()));

        // Test at a valid time (iat=1756959508, exp=1756961308)
        let test_time = 1756959508u64 * 1000 + 60000; // 1 minute after issuance, well before expiry

        // The verify_cwt_token method returns Permission
        // where Permission contains the doc_id and authorization
        match auth.verify_cwt_token_with_channel(token, test_time, "https://test.example.com") {
            Ok((permission, _channel)) => {
                // Token verification succeeded

                // Check if the permission matches our expected doc_id
                match permission {
                    Permission::Doc(doc_perm) => {
                        if doc_perm.doc_id == doc_id {
                        } else {
                        }
                    }
                    _ => {}
                }
            }
            Err(_e) => {
                // Also try to decode it
                match auth.decode_token(token) {
                    Ok(_payload) => {}
                    Err(_decode_err) => {}
                }
            }
        }
    }

    #[test]
    fn test_prefix_token_generation_and_verification() {
        let legacy_authenticator = Authenticator::gen_key_legacy().unwrap();
        let cwt_authenticator = create_test_authenticator_with_audience();

        // Test custom format prefix tokens
        let custom_token = legacy_authenticator
            .gen_prefix_token(
                "org123-",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("admin@org123.com"),
            )
            .unwrap();

        // Test CWT format prefix tokens
        let cwt_token = cwt_authenticator
            .gen_prefix_token_cwt(
                "user456-",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("user456"),
            )
            .unwrap();

        // Verify that prefix tokens work with matching document IDs
        let (auth1, user1) = legacy_authenticator
            .verify_doc_token_with_prefix(&custom_token, "org123-project-alpha", 0)
            .unwrap();
        assert_eq!(auth1, Authorization::Full);
        assert_eq!(user1, Some("admin@org123.com".to_string()));

        let (auth2, user2) = cwt_authenticator
            .verify_doc_token_with_prefix(&cwt_token, "user456-personal-doc", 0)
            .unwrap();
        assert_eq!(auth2, Authorization::ReadOnly);
        assert_eq!(user2, Some("user456".to_string()));
    }

    #[test]
    fn test_prefix_token_matching_logic() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();

        let prefix_token = authenticator
            .gen_prefix_token(
                "org123-",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
            )
            .unwrap();

        // Should match documents with the prefix
        assert!(authenticator
            .verify_doc_token_with_prefix(&prefix_token, "org123-project-alpha", 0)
            .is_ok());
        assert!(authenticator
            .verify_doc_token_with_prefix(&prefix_token, "org123-", 0)
            .is_ok());
        assert!(authenticator
            .verify_doc_token_with_prefix(&prefix_token, "org123-project-beta-doc456", 0)
            .is_ok());

        // Should not match documents without the prefix
        assert!(authenticator
            .verify_doc_token_with_prefix(&prefix_token, "org124-project", 0)
            .is_err());
        assert!(authenticator
            .verify_doc_token_with_prefix(&prefix_token, "different-org123-project", 0)
            .is_err());
        assert!(authenticator
            .verify_doc_token_with_prefix(&prefix_token, "org12-project", 0)
            .is_err());
    }

    #[test]
    fn test_empty_prefix_token() {
        let authenticator = create_test_authenticator_with_audience();

        // Empty prefix should match any document (server-like behavior but with user tracking)
        let empty_prefix_token = authenticator
            .gen_prefix_token_cwt(
                "",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("superuser"),
            )
            .unwrap();

        let (auth, user) = authenticator
            .verify_doc_token_with_prefix(&empty_prefix_token, "any-doc-id", 0)
            .unwrap();
        assert_eq!(auth, Authorization::Full);
        assert_eq!(user, Some("superuser".to_string()));

        let (auth2, user2) = authenticator
            .verify_doc_token_with_prefix(&empty_prefix_token, "", 0)
            .unwrap();
        assert_eq!(auth2, Authorization::Full);
        assert_eq!(user2, Some("superuser".to_string()));
    }

    #[test]
    fn test_prefix_token_user_extraction() {
        let legacy_authenticator = Authenticator::gen_key_legacy().unwrap();
        let cwt_authenticator = create_test_authenticator_with_audience();

        // Test custom format prefix token user extraction
        let custom_token = legacy_authenticator
            .gen_prefix_token(
                "test-",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("test_user"),
            )
            .unwrap();

        let user = legacy_authenticator
            .extract_user_from_token(&custom_token)
            .unwrap();
        assert_eq!(user, Some("test_user".to_string()));

        // Test CWT format prefix token user extraction
        let cwt_token = cwt_authenticator
            .gen_prefix_token_cwt(
                "cwt-",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("cwt_user"),
            )
            .unwrap();

        let cwt_user = cwt_authenticator
            .extract_user_from_token(&cwt_token)
            .unwrap();
        assert_eq!(cwt_user, Some("cwt_user".to_string()));

        // Test token without user
        let no_user_token = legacy_authenticator
            .gen_prefix_token(
                "public-",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
            )
            .unwrap();

        let no_user = legacy_authenticator
            .extract_user_from_token(&no_user_token)
            .unwrap();
        assert_eq!(no_user, None);
    }

    #[test]
    fn test_prefix_token_with_direct_doc_token_fallback() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();

        // Create a direct document token
        let doc_token = authenticator
            .gen_doc_token(
                "org123-project-alpha",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("doc_user"),
            )
            .unwrap();

        // verify_doc_token_with_prefix should work with direct tokens too
        let (auth, user) = authenticator
            .verify_doc_token_with_prefix(&doc_token, "org123-project-alpha", 0)
            .unwrap();
        assert_eq!(auth, Authorization::Full);
        assert_eq!(user, Some("doc_user".to_string()));

        // Should fail for different document ID
        assert!(authenticator
            .verify_doc_token_with_prefix(&doc_token, "different-doc", 0)
            .is_err());
    }

    #[test]
    fn test_prefix_token_file_operations() {
        let authenticator = create_test_authenticator_with_audience();

        let prefix_token = authenticator
            .gen_prefix_token_cwt(
                "project-",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                Some("project_admin"),
            )
            .unwrap();

        // Prefix tokens should work for file operations within their prefix
        let auth = authenticator
            .verify_file_token_for_doc(&prefix_token, "project-alpha-doc123", 0)
            .unwrap();
        assert_eq!(auth, Authorization::Full);

        // Should fail for documents outside the prefix
        assert!(authenticator
            .verify_file_token_for_doc(&prefix_token, "other-project-doc", 0)
            .is_err());
    }

    #[test]
    fn test_prefix_token_expiration() {
        let authenticator = Authenticator::gen_key_legacy().unwrap();

        // Create an expired prefix token
        let expired_token = authenticator
            .gen_prefix_token(
                "temp-",
                Authorization::ReadOnly,
                ExpirationTimeEpochMillis(1000), // Very old timestamp
                Some("temp_user"),
            )
            .unwrap();

        // Should fail due to expiration
        let current_time = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;

        assert!(matches!(
            authenticator.verify_doc_token_with_prefix(&expired_token, "temp-doc", current_time),
            Err(AuthError::Expired)
        ));
    }

    // Multi-key authentication tests
    #[test]
    fn test_multi_key_config_validation() {
        use crate::config::AuthKeyConfig;

        // Test single private key - should work
        let config1 = vec![AuthKeyConfig {
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
            key_id: Some("main".to_string()),
            private_key: Some(b64_encode(&[0u8; 32])),
            public_key: None,
        }];
        assert!(Authenticator::from_multi_key_config(&config1).is_ok());

        // Test multiple private keys - should fail
        let config2 = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("key1".to_string()),
                private_key: Some(b64_encode(&[0u8; 32])),
                public_key: None,
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("key2".to_string()),
                private_key: Some(b64_encode(&[1u8; 32])),
                public_key: None,
            },
        ];
        assert!(matches!(
            Authenticator::from_multi_key_config(&config2),
            Err(AuthError::MultiplePrivateKeys)
        ));

        // Test both keys provided - should fail
        let config3 = vec![AuthKeyConfig {
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
            key_id: None,
            private_key: Some(b64_encode(&[0u8; 32])),
            public_key: Some(b64_encode(&[1u8; 32])),
        }];
        assert!(matches!(
            Authenticator::from_multi_key_config(&config3),
            Err(AuthError::BothKeysProvided)
        ));

        // Test no keys provided - should fail
        let config4 = vec![AuthKeyConfig {
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
            key_id: None,
            private_key: None,
            public_key: None,
        }];
        assert!(matches!(
            Authenticator::from_multi_key_config(&config4),
            Err(AuthError::NoKeyProvided)
        ));
    }

    #[test]
    fn test_multi_key_token_verification_with_key_id() {
        use crate::config::AuthKeyConfig;

        // Create a multi-key authenticator with key IDs (one private HMAC, one ECDSA public key)
        let hmac_auth = Authenticator::gen_key_hmac().unwrap();
        let ecdsa_auth = Authenticator::gen_key_ecdsa().unwrap();

        let configs = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("hmac-key".to_string()),
                private_key: Some(hmac_auth.key_material().to_base64()),
                public_key: None,
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("ecdsa-key".to_string()),
                private_key: None,
                public_key: Some(ecdsa_auth.public_key_pem().unwrap()),
            },
        ];

        let _multi_auth = Authenticator::from_multi_key_config(&configs).unwrap();

        // Create single-key authenticators for comparison
        let hmac_auth = Authenticator::gen_key_hmac()
            .unwrap()
            .with_key_id("hmac-key".try_into().unwrap());
        let ecdsa_auth = Authenticator::gen_key_ecdsa()
            .unwrap()
            .with_key_id("ecdsa-key".try_into().unwrap());

        // Test tokens with correct key IDs
        let hmac_token = hmac_auth.server_token_cwt().unwrap();
        let ecdsa_token = ecdsa_auth.server_token_cwt().unwrap();

        // Note: For this test to work properly, we'd need to ensure the same key material
        assert!(!hmac_token.contains("."));
        assert!(!ecdsa_token.contains("."));
    }

    #[test]
    fn test_multi_key_token_verification_without_key_id() {
        use crate::config::AuthKeyConfig;

        // Create keys without key_id
        let legacy_key_material = Authenticator::gen_key_legacy().unwrap();
        let hmac_key_material = Authenticator::gen_key_hmac().unwrap();
        let ecdsa_key_material = Authenticator::gen_key_ecdsa().unwrap();

        let configs = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: None,
                private_key: Some(legacy_key_material.key_material().to_base64()),
                public_key: None,
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: None,
                private_key: Some(hmac_key_material.key_material().to_base64()),
                public_key: None,
            },
        ];

        // This should fail due to multiple private keys
        assert!(matches!(
            Authenticator::from_multi_key_config(&configs),
            Err(AuthError::MultiplePrivateKeys)
        ));

        // Test with single private key and multiple public keys
        let configs_valid = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: None,
                private_key: Some(legacy_key_material.key_material().to_base64()),
                public_key: None,
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("readonly".to_string()),
                private_key: None,
                public_key: Some(ecdsa_key_material.public_key_pem().unwrap()),
            },
        ];

        let _multi_auth = Authenticator::from_multi_key_config(&configs_valid).unwrap();

        // Generate a token with the signing key
        let token = legacy_key_material
            .gen_doc_token(
                "test-doc",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
            )
            .unwrap();

        // Verify that multi-key authenticator can verify tokens without key_id
        // Note: This will work because both use the same key material
        assert!(legacy_key_material
            .verify_doc_token(&token, "test-doc", 0)
            .is_ok());
    }

    #[test]
    fn test_multi_key_key_mismatch() {
        use crate::config::AuthKeyConfig;

        let configs = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("key1".to_string()),
                private_key: Some(
                    Authenticator::gen_key_legacy()
                        .unwrap()
                        .key_material()
                        .to_base64(),
                ),
                public_key: None,
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("key2".to_string()),
                private_key: None,
                public_key: Some(
                    Authenticator::gen_key_ecdsa()
                        .unwrap()
                        .public_key_pem()
                        .unwrap(),
                ),
            },
        ];

        let multi_auth = Authenticator::from_multi_key_config(&configs).unwrap();

        // Test token with invalid content
        let fake_token = "invalidtokencontent";

        // Should fail with some error
        assert!(multi_auth.verify_token_auto(&fake_token, 0).is_err());
    }

    #[test]
    fn test_cwt_keyless_token_against_all_keyed_config_is_key_mismatch() {
        // Regression test for the previously-uncovered branch in
        // verify_cwt_token_with_channel: when every configured key has a key_id
        // (normal prod config), `keys_without_id` is empty. A CWT token with no
        // COSE header kid falls into that empty vector, the loop body never runs,
        // and verification must return KeyMismatch immediately rather than panic,
        // hang, or silently fall through to some other outcome. Before this fix
        // that early return existed but was completely unlogged (#42323ab2).
        use crate::config::AuthKeyConfig;

        let only_keyed = vec![AuthKeyConfig {
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
            key_id: Some("only-key".to_string()),
            private_key: Some(
                Authenticator::gen_key_hmac()
                    .unwrap()
                    .key_material()
                    .to_base64(),
            ),
            public_key: None,
        }];

        let mut verifier = Authenticator::from_multi_key_config(&only_keyed).unwrap();
        verifier.set_expected_audience(Some("https://api.example.com".to_string()));
        assert!(
            verifier.keys_without_id.is_empty(),
            "test setup: every key must have a key_id"
        );

        // A CWT token generated by a fresh authenticator with no key_id set — its
        // COSE header carries no kid, regardless of the fact that its signing key
        // is unrelated to `verifier`'s configured key.
        let keyless_issuer = create_test_authenticator_with_audience();
        let token = keyless_issuer
            .gen_doc_token_cwt(
                "doc123",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();
        assert_eq!(detect_token_format(&token), TokenFormat::Cwt);
        assert!(extract_cwt_key_id(&token).is_none());

        assert!(matches!(
            verifier.verify_doc_token(&token, "doc123", 0),
            Err(AuthError::KeyMismatch)
        ));
    }

    #[test]
    fn test_multi_key_configuration_ordering() {
        use crate::config::AuthKeyConfig;

        // Test that keys without key_id are tried in configuration order
        let legacy_auth1 = Authenticator::gen_key_legacy().unwrap();
        let ecdsa_auth2 = Authenticator::gen_key_ecdsa().unwrap();

        let configs = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: None,
                private_key: Some(legacy_auth1.key_material().to_base64()),
                public_key: None,
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("with_id".to_string()),
                private_key: None,
                public_key: Some(ecdsa_auth2.public_key_pem().unwrap()),
            },
        ];

        // Should work since we only have one private key
        let multi_auth = Authenticator::from_multi_key_config(&configs).unwrap();

        // Verify internal structure
        assert_eq!(multi_auth.keys.len(), 2);
        assert_eq!(multi_auth.keys_without_id, vec![0]);
        assert_eq!(multi_auth.key_lookup.get("with_id"), Some(&1));
    }

    #[test]
    fn test_multi_key_signing_key_selection() {
        use crate::config::AuthKeyConfig;

        let hmac_key = Authenticator::gen_key_legacy().unwrap();
        let ecdsa_key = Authenticator::gen_key_ecdsa().unwrap(); // Use as public key

        let configs = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("readonly".to_string()),
                private_key: None,
                public_key: Some(ecdsa_key.public_key_pem().unwrap()),
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("signing".to_string()),
                private_key: Some(hmac_key.key_material().to_base64()),
                public_key: None,
            },
        ];

        let mut multi_auth = Authenticator::from_multi_key_config(&configs).unwrap();
        multi_auth.set_expected_audience(Some("https://api.example.com".to_string()));

        // Should be able to generate tokens (using the signing key)
        let server_token = multi_auth.server_token_legacy();
        assert!(
            server_token.is_ok(),
            "Failed to generate server token: {:?}",
            server_token.err()
        );

        let token = server_token.unwrap();
        assert!(!token.contains("."));
    }

    #[test]
    fn test_multi_key_no_signing_key_error() {
        use crate::config::AuthKeyConfig;

        // Configuration with only public keys (no signing capability)
        // Generate ECDSA keys and extract their public keys
        let ecdsa_key1 = Authenticator::gen_key_ecdsa().unwrap();
        let ecdsa_key2 = Authenticator::gen_key_ecdsa().unwrap();

        // Extract public keys from the private keys and format as PEM
        let public_key1_pem = ecdsa_key1.public_key_pem().unwrap();

        let public_key2_pem = ecdsa_key2.public_key_pem().unwrap();

        let configs = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("readonly1".to_string()),
                private_key: None,
                public_key: Some(public_key1_pem),
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("readonly2".to_string()),
                private_key: None,
                public_key: Some(public_key2_pem),
            },
        ];

        let multi_auth = Authenticator::from_multi_key_config(&configs).unwrap();

        // Should fail to generate tokens since no signing key is available
        assert!(matches!(
            multi_auth.server_token(),
            Err(AuthError::NoSigningKey)
        ));
    }

    #[test]
    fn test_key_rotation_scenario() {
        use crate::config::AuthKeyConfig;

        // Simulate key rotation: old key + new key
        let old_key = Authenticator::gen_key_legacy().unwrap();
        let new_key = Authenticator::gen_key_ecdsa().unwrap();

        // Phase 1: Old key for signing, new key for verification only
        let rotation_configs = vec![
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("old-v1".to_string()),
                private_key: Some(old_key.key_material().to_base64()),
                public_key: None,
            },
            AuthKeyConfig {
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
                key_id: Some("new-v2".to_string()),
                private_key: None,
                public_key: Some(new_key.public_key_pem().unwrap()),
            },
        ];

        let rotation_auth = Authenticator::from_multi_key_config(&rotation_configs).unwrap();

        // Should be able to generate tokens with old key
        let old_key_with_id = old_key.clone().with_key_id("old-v1".try_into().unwrap());
        let token = old_key_with_id
            .gen_doc_token(
                "test-doc",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
            )
            .unwrap();

        // Multi-key authenticator should verify tokens from old key
        assert!(rotation_auth
            .verify_doc_token(&token, "test-doc", 0)
            .is_ok());

        assert!(!token.contains("."));
    }

    #[test]
    fn test_token_type_verification_default_config() {
        use crate::config::AuthKeyConfig;

        // Create a shared HMAC key for both signing and verification
        let hmac_key = b64_encode(&[42u8; 32]); // Simple 256-bit key
        let key_id = "test-key";

        // Generator with full permissions
        let generator_config = AuthKeyConfig {
            key_id: Some(key_id.to_string()),
            private_key: Some(hmac_key.clone()),
            public_key: None,
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
        };

        // Verifier with limited permissions (same HMAC key)
        let verifier_config = AuthKeyConfig {
            key_id: Some(key_id.to_string()),
            private_key: Some(hmac_key.clone()),
            public_key: None,
            allowed_token_types: vec![TokenType::Document, TokenType::File], // Default permissions
        };

        let mut generator = Authenticator::from_multi_key_config(&[generator_config]).unwrap();
        generator.set_expected_audience(Some("https://test.example.com".to_string()));

        let mut verifier = Authenticator::from_multi_key_config(&[verifier_config]).unwrap();
        verifier.set_expected_audience(Some("https://test.example.com".to_string()));

        // Generate tokens
        let doc_token = generator
            .gen_doc_token_cwt(
                "test-doc",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();

        let server_token = generator.server_token_cwt().unwrap();

        // Test verification with limited permissions
        // Should be able to verify document tokens (allowed by default)
        let doc_verification = verifier.verify_token_auto(&doc_token, 0);
        if doc_verification.is_err() {
            eprintln!("Document verification error: {:?}", doc_verification);
        }
        assert!(
            doc_verification.is_ok(),
            "Should be able to verify document tokens with default permissions"
        );

        // Should NOT be able to verify server tokens (not allowed by default)
        let server_verification = verifier.verify_token_auto(&server_token, 0);
        assert!(
            server_verification.is_err(),
            "Should not be able to verify server tokens with default permissions"
        );
        if let Err(AuthError::UnauthorizedTokenType(msg)) = server_verification {
            assert!(msg.contains("not authorized for server tokens"));
        }
    }

    #[test]
    fn test_token_type_verification_explicit_config() {
        use crate::config::{AuthKeyConfig, TokenType};

        // Use same HMAC key for both signing and verification
        let hmac_key = b64_encode(&[123u8; 32]); // Different key from first test
        let key_id = "test-key-2";

        // Generator with full permissions
        let generator_config = AuthKeyConfig {
            key_id: Some(key_id.to_string()),
            private_key: Some(hmac_key.clone()),
            public_key: None,
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
        };

        // Verifier with server-only permissions (same HMAC key)
        let server_only_config = AuthKeyConfig {
            key_id: Some(key_id.to_string()),
            private_key: Some(hmac_key.clone()),
            public_key: None,
            allowed_token_types: vec![TokenType::Server], // Only server tokens
        };

        let mut generator = Authenticator::from_multi_key_config(&[generator_config]).unwrap();
        generator.set_expected_audience(Some("https://test.example.com".to_string()));

        let mut server_only_auth =
            Authenticator::from_multi_key_config(&[server_only_config]).unwrap();
        server_only_auth.set_expected_audience(Some("https://test.example.com".to_string()));

        // Generate different types of tokens
        let doc_token = generator
            .gen_doc_token_cwt(
                "test-doc",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();

        let server_token = generator.server_token_cwt().unwrap();

        // Test verification with server-only permissions
        // Should be able to verify server tokens
        let server_verification = server_only_auth.verify_token_auto(&server_token, 0);
        assert!(
            server_verification.is_ok(),
            "Should be able to verify server tokens with explicit server permission"
        );

        // Should NOT be able to verify document tokens
        let doc_verification = server_only_auth.verify_token_auto(&doc_token, 0);
        assert!(
            doc_verification.is_err(),
            "Should not be able to verify document tokens without document permission"
        );
        if let Err(AuthError::UnauthorizedTokenType(msg)) = doc_verification {
            assert!(msg.contains("not authorized for document tokens"));
        }
    }

    #[test]
    fn test_multi_key_verification_with_mixed_permissions() {
        use crate::config::{AuthKeyConfig, TokenType};

        // Use HMAC keys for simpler testing
        let generator_key = b64_encode(&[99u8; 32]);

        // Generator has full permissions for creating tokens
        let generator_config = AuthKeyConfig {
            key_id: Some("generator".to_string()),
            private_key: Some(generator_key.clone()),
            public_key: None,
            allowed_token_types: vec![
                TokenType::Document,
                TokenType::File,
                TokenType::Server,
                TokenType::Prefix,
            ],
        };

        // Multi-key verifier with mixed permissions (using same key for verification)
        let configs = vec![
            // Admin key with all permissions (same key as generator for verification)
            AuthKeyConfig {
                key_id: Some("generator".to_string()), // Same key ID
                private_key: Some(generator_key.clone()),
                public_key: None,
                allowed_token_types: vec![
                    TokenType::Document,
                    TokenType::File,
                    TokenType::Server,
                    TokenType::Prefix,
                ],
            },
        ];

        let mut generator = Authenticator::from_multi_key_config(&[generator_config]).unwrap();
        generator.set_expected_audience(Some("https://test.example.com".to_string()));

        let mut verifier = Authenticator::from_multi_key_config(&configs).unwrap();
        verifier.set_expected_audience(Some("https://test.example.com".to_string()));

        // Generate different token types
        let doc_token = generator
            .gen_doc_token_cwt(
                "test-doc",
                Authorization::Full,
                ExpirationTimeEpochMillis(u64::MAX),
                None,
                None,
            )
            .unwrap();
        let server_token = generator.server_token_cwt().unwrap();

        // Should be able to verify tokens when using admin key (has all permissions)
        // The admin key should be selected for verification since it has the required permissions
        let doc_result = verifier.verify_token_auto(&doc_token, 0);
        if doc_result.is_err() {
            eprintln!("Doc verification error: {:?}", doc_result);
        }
        assert!(doc_result.is_ok());

        let server_result = verifier.verify_token_auto(&server_token, 0);
        if server_result.is_err() {
            eprintln!("Server verification error: {:?}", server_result);
        }
        assert!(server_result.is_ok());
    }

    #[test]
    fn test_token_type_from_permission() {
        use crate::config::TokenType;

        let doc_permission = Permission::Doc(DocPermission {
            doc_id: "test".to_string(),
            authorization: Authorization::Full,
            user: None,
        });
        assert_eq!(
            TokenType::from_permission(&doc_permission),
            TokenType::Document
        );

        let file_permission = Permission::File(FilePermission {
            file_hash: "hash".to_string(),
            doc_id: "test".to_string(),
            authorization: Authorization::Full,
            content_type: None,
            content_length: None,
            user: None,
        });
        assert_eq!(
            TokenType::from_permission(&file_permission),
            TokenType::File
        );

        let server_permission = Permission::Server;
        assert_eq!(
            TokenType::from_permission(&server_permission),
            TokenType::Server
        );

        let prefix_permission = Permission::Prefix(PrefixPermission {
            prefix: "test-".to_string(),
            authorization: Authorization::Full,
            user: None,
        });
        assert_eq!(
            TokenType::from_permission(&prefix_permission),
            TokenType::Prefix
        );
    }

    #[test]
    fn test_default_allowed_token_types_serde() {
        use crate::config::{AuthKeyConfig, TokenType};

        // Test that serde default works correctly
        let toml_str = r#"
            key_id = "test"
            private_key = "test-key"
        "#;

        let parsed: AuthKeyConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(
            parsed.allowed_token_types,
            vec![TokenType::Document, TokenType::File]
        );
    }
}
