//! The two primitives Aver has not got, behind the `Primitives` capability
//! contract.
//!
//! Neither is written here. RIPEMD-160 comes from the `ripemd` crate and
//! secp256k1 verification from `libsecp256k1` through the `secp256k1` crate,
//! which is the implementation Bitcoin Core itself uses. That is the whole
//! reason this is a provider rather than Aver: a consensus rule is not a good
//! place to find out that a hand-written curve has an edge case.

use std::sync::Arc;

use aver_rt::provider::{
    CapabilityProvider, ProviderBinding, ProviderContext, ProviderFault, ProviderValue,
};
use ripemd::{Digest, Ripemd160};

/// Pinned to the contract in `primitives.av`. A mismatch fails at startup
/// rather than at the first call.
pub const CONTRACT_HASH: &str =
    "sha256:a4ada0e3b33296aadc557423913af5e456be0c8395bf7b89e6bae8298c8991ec";

const CARRIER: &str = "Domain.Primitives.Octets";

struct Primitives;

fn octets_in(value: &ProviderValue, what: &str) -> Result<Vec<u8>, ProviderFault> {
    let ProviderValue::Record { fields, .. } = value else {
        return Err(ProviderFault::new("bad_shape", format!("{what} is not Octets")));
    };
    let Some((_, ProviderValue::List(items))) = fields.iter().find(|(n, _)| n == "values") else {
        return Err(ProviderFault::new("bad_shape", format!("{what} has no values list")));
    };
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let ProviderValue::Int(n) = item else {
            return Err(ProviderFault::new("bad_shape", format!("{what} holds a non-Int")));
        };
        let n = n
            .to_i64()
            .ok_or_else(|| ProviderFault::new("out_of_range", format!("{what} byte is huge")))?;
        out.push(u8::try_from(n).map_err(|_| {
            ProviderFault::new("out_of_range", format!("{what} byte {n} is not 0-255"))
        })?);
    }
    Ok(out)
}

fn octets_out(bytes: &[u8]) -> ProviderValue {
    ProviderValue::Record {
        type_name: CARRIER.to_string(),
        fields: vec![(
            "values".to_string(),
            ProviderValue::List(
                bytes
                    .iter()
                    .map(|b| ProviderValue::Int(i64::from(*b).into()))
                    .collect(),
            ),
        )],
    }
}

fn ripemd160(input: &[u8]) -> [u8; 20] {
    let mut hasher = Ripemd160::new();
    hasher.update(input);
    hasher.finalize().into()
}

/// Whether `signature` is a valid ECDSA signature by `public_key` over
/// `message`.
///
/// Returns `false` rather than faulting on malformed input: the caller refuses
/// what it can before it gets here, and anything still malformed at this point
/// is a signature that does not verify, not a broken provider.
fn verify(public_key: &[u8], signature: &[u8], message: &[u8]) -> bool {
    use secp256k1::{ecdsa::Signature, Message, PublicKey, Secp256k1};
    let Ok(key) = PublicKey::from_slice(public_key) else {
        return false;
    };
    // Lax DER, not strict. Before BIP66 activated at height 363725 in July
    // 2015, Bitcoin parsed signatures with OpenSSL, which accepted encodings
    // strict DER forbids -- most commonly an `s` whose leading byte has the
    // high bit set and no 0x00 padding, which strict DER would read as a
    // negative integer. Those transactions are on the chain and must still
    // validate, so this is the same lax parser Bitcoin Core keeps for exactly
    // this reason (`ecdsa_signature_parse_der_lax`, used by CPubKey::Verify).
    //
    // BIP66 itself is a rule about a height, not about arithmetic. Enforcing
    // it belongs to Domain.Ecdsa alongside the other policy checks, not here.
    let Ok(mut sig) = Signature::from_der_lax(signature) else {
        return false;
    };
    // Bitcoin accepts high-S signatures in old blocks; libsecp256k1 will not
    // verify them, so normalise before asking. BIP146 low-S enforcement is the
    // caller's policy decision and is made in Domain.Ecdsa, not here.
    sig.normalize_s();
    if message.len() != 32 {
        return false;
    }
    let Ok(msg) = Message::from_digest_slice(message) else {
        return false;
    };
    Secp256k1::verification_only().verify_ecdsa(&msg, &sig, &key).is_ok()
}

impl CapabilityProvider for Primitives {
    fn identity(&self) -> &str {
        "btc-listener.primitives/ripemd+libsecp256k1@1"
    }

    fn fingerprint(&self) -> &str {
        concat!("ripemd 0.1, secp256k1 0.29, built ", env!("CARGO_PKG_VERSION"))
    }

    fn invoke(
        &self,
        context: &ProviderContext,
        args: &[ProviderValue],
    ) -> Result<ProviderValue, ProviderFault> {
        match context.operation.as_str() {
            "Domain.Primitives.ripemd160" => {
                let [input] = args else {
                    return Err(ProviderFault::new("bad_arity", "ripemd160 takes one Octets"));
                };
                Ok(octets_out(&ripemd160(&octets_in(input, "input")?)))
            }
            "Domain.Primitives.verifySignature" => {
                let [key, sig, msg] = args else {
                    return Err(ProviderFault::new(
                        "bad_arity",
                        "verifySignature takes three Octets",
                    ));
                };
                Ok(ProviderValue::Bool(verify(
                    &octets_in(key, "publicKey")?,
                    &octets_in(sig, "signature")?,
                    &octets_in(msg, "message")?,
                )))
            }
            other => Err(ProviderFault::new("bad_operation", other)),
        }
    }
}

/// The zero-argument factory `aver.toml` names.
pub fn primitives_binding() -> ProviderBinding {
    ProviderBinding::new(
        "Domain.Primitives",
        CONTRACT_HASH,
        [
            "Domain.Primitives.ripemd160",
            "Domain.Primitives.verifySignature",
        ],
        Arc::new(Primitives),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::Digest as _;

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
    }

    /// The eight vectors published with the RIPEMD-160 specification. This is
    /// the test the Aver corpus can no longer run, so it has to live here.
    #[test]
    fn ripemd160_matches_every_published_vector() {
        let cases: [(&[u8], &str); 8] = [
            (b"", "9c1185a5c5e9fc54612808977ee8f548b2258d31"),
            (b"a", "0bdc9d2d256b3ee9daae347be6f4dc835a467ffe"),
            (b"abc", "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc"),
            (b"message digest", "5d0689ef49d2fae572b881b123a85ffa21595f36"),
            (
                b"abcdefghijklmnopqrstuvwxyz",
                "f71c27109c692c1b56bbdceb5b9d2865b3708dbc",
            ),
            (
                b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
                "12a053384a9c0c88e405a06c27dcf49ada62eb2b",
            ),
            (
                b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
                "b0e20b6e3116640286ed3a87a5713079b21f5189",
            ),
            (
                b"12345678901234567890123456789012345678901234567890123456789012345678901234567890",
                "9b752e45573d4b39f4dbd3323cab82bf63326bfb",
            ),
        ];
        for (input, want) in cases {
            assert_eq!(hex(&ripemd160(input)), want, "ripemd160 of {input:?}");
        }
    }

    /// hash160 of the empty input, which every P2SH commitment is a cousin of.
    #[test]
    fn hash160_of_empty_is_the_known_value() {
        let sha = sha2::Sha256::digest(b"");
        assert_eq!(
            hex(&ripemd160(&sha)),
            "b472a266d0bd89c13706a4132ccfb16f7c3b9fcb"
        );
    }

    /// A real mainnet signature whose `s` is not canonical DER: 32 bytes with
    /// the high bit set and no 0x00 padding. Legal in 2012, refused by strict
    /// DER, and on the chain — block 170004, transaction
    /// 4020efeaed3a4a8eeb32876624a6f3ce1de6c8d3c53ed4f7b44f24e277bfa16c.
    ///
    /// This is what n1bor/btc-listener#18 turned out to be: about 1% of real
    /// spends of that era, failing because the parser was stricter than the
    /// consensus rule of the day.
    #[test]
    fn verifies_a_signature_that_is_not_canonical_der() {
        let key = hex_bytes("04a44b41f64ffd78919a05c980df85e93cb2c9fa0d245d3e582bd8adcdce7c75572b58d55b128d8d337cd6b567c43ac6d8af2e2d7957beee34631a8c038e128088");
        let sig = hex_bytes("30440220638f5d3b899b257fa5caa54f5968363f40fd99ae837d507b18e6d8e067dd75870220a80a4b50980a61f5369524ec7cd425cae68740936bf385b6518275318fae42e6");
        let msg = hex_bytes("54917b4bc20a330b58cb9d12006b57623e64d1a9cb0dc1af1f17849075d84f6d");
        assert_eq!(sig[38], 0xa8, "the s value must still have its high bit set");

        // The failure mode is worse than a refusal. `from_der` does not reject
        // this; it reports success and hands back a signature whose s is zero,
        // which then never verifies. A parse error would at least have been
        // visible.
        let strict = secp256k1::ecdsa::Signature::from_der(&sig)
            .expect("strict parsing reports success on this input");
        assert_eq!(
            &hex(&strict.serialize_compact())[64..],
            "0000000000000000000000000000000000000000000000000000000000000000",
            "strict parsing is expected to zero the s value here"
        );

        let lax = secp256k1::ecdsa::Signature::from_der_lax(&sig).expect("lax parses");
        assert_eq!(
            &hex(&lax.serialize_compact())[64..],
            "a80a4b50980a61f5369524ec7cd425cae68740936bf385b6518275318fae42e6",
            "lax parsing keeps the s value the signer meant"
        );

        assert!(verify(&key, &sig, &msg), "a pre-BIP66 signature must verify");
    }

    /// A real mainnet signature: the first Bitcoin spend ever made, block 170.
    /// Key, DER signature and legacy sighash all come from that transaction.
    #[test]
    fn verifies_the_first_bitcoin_spend() {
        let key = hex_bytes("0411db93e1dcdb8a016b49840f8c53bc1eb68a382e97b1482ecad7b148a6909a5cb2e0eaddfb84ccf9744464f82e160bfa9b8b64f9d4c03f999b8643f656b412a3");
        let sig = hex_bytes("304402204e45e16932b8af514961a1d3a1a25fdf3f4f7732e9d624c6c61548ab5fb8cd410220181522ec8eca07de4860a4acdd12909d831cc56cbbac4622082221a8768d1d09");
        let msg = hex_bytes("7a05c6145f10101e9d6325494245adf1297d80f8f38d4d576d57cdba220bcb19");
        assert!(verify(&key, &sig, &msg), "the first spend must verify");
    }

    #[test]
    fn refuses_a_signature_over_a_different_message() {
        let key = hex_bytes("0411db93e1dcdb8a016b49840f8c53bc1eb68a382e97b1482ecad7b148a6909a5cb2e0eaddfb84ccf9744464f82e160bfa9b8b64f9d4c03f999b8643f656b412a3");
        let sig = hex_bytes("304402204e45e16932b8af514961a1d3a1a25fdf3f4f7732e9d624c6c61548ab5fb8cd410220181522ec8eca07de4860a4acdd12909d831cc56cbbac4622082221a8768d1d09");
        let mut msg = hex_bytes("7a05c6145f10101e9d6325494245adf1297d80f8f38d4d576d57cdba220bcb19");
        msg[0] ^= 1;
        assert!(!verify(&key, &sig, &msg));
    }

    #[test]
    fn malformed_input_is_false_and_never_a_fault() {
        assert!(!verify(b"", b"", b""));
        assert!(!verify(&[2u8; 33], &[0u8; 8], &[0u8; 32]));
    }

    fn hex_bytes(s: &str) -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("hex"))
            .collect()
    }
}
