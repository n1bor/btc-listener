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
    "sha256:59e784f40ab87491c408214c48a1efe612004657f86f7315c1ed9107cc4635ec";

struct Primitives;

/// The bytes of a `Bytes`. The contract takes the standard type now, so this
/// is a match rather than a walk: jasisz/aver#1022 gave `Bytes` a
/// compiler-owned capability ABI carried as `ProviderValue::Bytes(Vec<u8>)`.
/// What stood here before unwrapped a capability-owned `Octets` record and
/// range-checked every element of it, on every call, for data that had come
/// out of a `Bytes` and was octets by construction.
fn bytes_in<'a>(value: &'a ProviderValue, what: &str) -> Result<&'a [u8], ProviderFault> {
    match value {
        ProviderValue::Bytes(bytes) => Ok(bytes),
        _ => Err(ProviderFault::new(
            "bad_shape",
            format!("{what} is not Bytes"),
        )),
    }
}

fn sha1(input: &[u8]) -> [u8; 20] {
    use sha1::Sha1;
    let mut hasher = Sha1::new();
    hasher.update(input);
    hasher.finalize().into()
}

fn ripemd160(input: &[u8]) -> [u8; 20] {
    let mut hasher = Ripemd160::new();
    hasher.update(input);
    hasher.finalize().into()
}

fn verify_schnorr(public_key: &[u8], signature: &[u8], message: &[u8]) -> bool {
    use secp256k1::{schnorr::Signature, Message, Secp256k1, XOnlyPublicKey};
    // BIP340 fixes all three widths.  Anything else is not a signature that
    // fails to verify, it is not a signature, and the answer is the same.
    if public_key.len() != 32 || signature.len() != 64 || message.len() != 32 {
        return false;
    }
    let key = match XOnlyPublicKey::from_slice(public_key) {
        Ok(k) => k,
        Err(_) => return false,
    };
    let sig = match Signature::from_slice(signature) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let msg = Message::from_digest_slice(message).expect("checked 32 bytes above");
    Secp256k1::verification_only().verify_schnorr(&sig, &msg, &key).is_ok()
}

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
                    return Err(ProviderFault::new("bad_arity", "ripemd160 takes one Bytes"));
                };
                Ok(ProviderValue::Bytes(
                    ripemd160(bytes_in(input, "input")?).to_vec(),
                ))
            }
            "Domain.Primitives.sha1" => {
                let [input] = args else {
                    return Err(ProviderFault::new("bad_arity", "sha1 takes one Bytes"));
                };
                Ok(ProviderValue::Bytes(sha1(bytes_in(input, "input")?).to_vec()))
            }
            "Domain.Primitives.verifySchnorr" => {
                match args {
                    [public_key, signature, message] => Ok(ProviderValue::Bool(verify_schnorr(
                        bytes_in(public_key, "verifySchnorr public key")?,
                        bytes_in(signature, "verifySchnorr signature")?,
                        bytes_in(message, "verifySchnorr message")?,
                    ))),
                    _ => Err(ProviderFault::new("bad_arity", "verifySchnorr takes three Bytes")),
                }
            }
            "Domain.Primitives.verifySignature" => {
                let [key, sig, msg] = args else {
                    return Err(ProviderFault::new(
                        "bad_arity",
                        "verifySignature takes three Bytes",
                    ));
                };
                Ok(ProviderValue::Bool(verify(
                    bytes_in(key, "publicKey")?,
                    bytes_in(sig, "signature")?,
                    bytes_in(msg, "message")?,
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
            "Domain.Primitives.sha1",
            "Domain.Primitives.verifySignature",
            "Domain.Primitives.verifySchnorr",
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



    /// BIP340's own test-vectors.csv, every vector whose message is the
    /// thirty-two bytes Bitcoin signs.  15 of the 19, and 10 of those 15 are
    /// supposed to fail -- a key off the curve, s at the group order, a
    /// forged r.  The failing ones are the point: an implementation that ran
    /// only the passing ones would be indistinguishable from `true`.
    ///
    /// The 4 excluded vectors were added to the BIP in 2022 to cover
    /// messages of other lengths (0, 1, 17 and 100 bytes).  BIP341 signs a
    /// thirty-two byte message and nothing else, this contract says so, and
    /// libsecp256k1's schnorrsig_verify is called here with a fixed length of
    /// thirty-two -- so those vectors are out of scope rather than failing.
    #[test]
    fn every_bip340_vector_over_a_32_byte_message() {
        let vectors: &[(&str, &str, &str, bool, &str)] = &[
        ("F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9", "0000000000000000000000000000000000000000000000000000000000000000", "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA821525F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0", true, ""),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "6896BD60EEAE296DB48A229FF71DFE071BDE413E6D43F917DC8DCF8C78DE33418906D11AC976ABCCB20B091292BFF4EA897EFCB639EA871CFA95F6DE339E4B0A", true, ""),
        ("DD308AFEC5777E13121FA72B9CC1B7CC0139715309B086C960E18FD969774EB8", "7E2D58D8B3BCDF1ABADEC7829054F90DDA9805AAB56C77333024B9D0A508B75C", "5831AAEED7B44BB74E5EAB94BA9D4294C49BCF2A60728D8B4C200F50DD313C1BAB745879A5AD954A72C45A91C3A51D3C7ADEA98D82F8481E0E1E03674A6F3FB7", true, ""),
        ("25D1DFF95105F5253C4022F628A996AD3A0D95FBF21D468A1B33F8C160D8F517", "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", "7EB0509757E246F19449885651611CB965ECC1A187DD51B64FDA1EDC9637D5EC97582B9CB13DB3933705B32BA982AF5AF25FD78881EBB32771FC5922EFC66EA3", true, "test fails if msg is reduced modulo p or n"),
        ("D69C3509BB99E412E68B0FE8544E72837DFA30746D8BE2AA65975F29D22DC7B9", "4DF3C3F68FCC83B27E9D42C90431A72499F17875C81A599B566C9889B9696703", "00000000000000000000003B78CE563F89A0ED9414F5AA28AD0D96D6795F9C6376AFB1548AF603B3EB45C9F8207DEE1060CB71C04E80F593060B07D28308D7F4", true, ""),
        ("EEFDEA4CDB677750A420FEE807EACF21EB9898AE79B9768766E4FAA04A2D4A34", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "6CFF5C3BA86C69EA4B7376F31A9BCB4F74C1976089B2D9963DA2E5543E17776969E89B4C5564D00349106B8497785DD7D1D713A8AE82B32FA79D5F7FC407D39B", false, "public key not on the curve"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "FFF97BD5755EEEA420453A14355235D382F6472F8568A18B2F057A14602975563CC27944640AC607CD107AE10923D9EF7A73C643E166BE5EBEAFA34B1AC553E2", false, "has_even_y(R) is false"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "1FA62E331EDBC21C394792D2AB1100A7B432B013DF3F6FF4F99FCB33E0E1515F28890B3EDB6E7189B630448B515CE4F8622A954CFE545735AAEA5134FCCDB2BD", false, "negated message"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "6CFF5C3BA86C69EA4B7376F31A9BCB4F74C1976089B2D9963DA2E5543E177769961764B3AA9B2FFCB6EF947B6887A226E8D7C93E00C5ED0C1834FF0D0C2E6DA6", false, "negated s value"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "0000000000000000000000000000000000000000000000000000000000000000123DDA8328AF9C23A94C1FEECFD123BA4FB73476F0D594DCB65C6425BD186051", false, "sG - eP is infinite. Test fails in single verification if has_even_y(inf) is defined as true and x(inf) as 0"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "00000000000000000000000000000000000000000000000000000000000000017615FBAF5AE28864013C099742DEADB4DBA87F11AC6754F93780D5A1837CF197", false, "sG - eP is infinite. Test fails in single verification if has_even_y(inf) is defined as true and x(inf) as 1"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "4A298DACAE57395A15D0795DDBFD1DCB564DA82B0F269BC70A74F8220429BA1D69E89B4C5564D00349106B8497785DD7D1D713A8AE82B32FA79D5F7FC407D39B", false, "sig[0:32] is not an X coordinate on the curve"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F69E89B4C5564D00349106B8497785DD7D1D713A8AE82B32FA79D5F7FC407D39B", false, "sig[0:32] is equal to field size"),
        ("DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "6CFF5C3BA86C69EA4B7376F31A9BCB4F74C1976089B2D9963DA2E5543E177769FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", false, "sig[32:64] is equal to curve order"),
        ("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC30", "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89", "6CFF5C3BA86C69EA4B7376F31A9BCB4F74C1976089B2D9963DA2E5543E17776969E89B4C5564D00349106B8497785DD7D1D713A8AE82B32FA79D5F7FC407D39B", false, "public key is not a valid X coordinate because it exceeds the field size"),
        ];
        assert_eq!(vectors.len(), 15, "vector table truncated");
        for (key, message, signature, want, comment) in vectors {
            let got = verify_schnorr(
                &hex_bytes(key),
                &hex_bytes(signature),
                &hex_bytes(message),
            );
            assert_eq!(got, *want, "BIP340 vector key={} comment={}", key, comment);
        }
    }

    fn hex_bytes(s: &str) -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("hex"))
            .collect()
    }
}
