"""TLS for the messenger server.

On first start the server creates its own certificate (no certificate authority needed on a LAN).
Clients remember its SHA-256 fingerprint the first time they connect ("trust on first use") and
refuse a server that later presents a different certificate.
"""

import datetime
import hashlib
import logging
import os
import socket
import ssl

log = logging.getLogger("server")


def fingerprint(der: bytes) -> str:
    """'AB:CD:...' SHA-256 of a DER certificate (same format as the client shows)."""
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


def ensure_certificate(folder: str, common_name: str):
    """Create (once) and return (cert_path, key_path)."""
    cert_path = os.path.join(folder, "server.crt")
    key_path = os.path.join(folder, "server.key")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    if os.path.exists(cert_path) or os.path.exists(key_path):
        log.warning("Only one of server.crt / server.key exists in %s - creating a NEW certificate. Every client "
                    "will ask once to trust the server's new identity. (Restore both files from a backup to keep "
                    "the old identity.)", folder)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    os.makedirs(folder, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (common_name or "LAN Messenger")[:60]),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LAN Messenger")])
    now = datetime.datetime.now(datetime.timezone.utc)
    alt = [x509.DNSName(socket.gethostname()), x509.DNSName("localhost")]
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365 * 20))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .sign(key, hashes.SHA256()))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    log.info("Created the server's TLS certificate in %s", folder)
    return cert_path, key_path


def server_context(cert_path: str, key_path: str):
    """Returns (ssl_context, fingerprint)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    with open(cert_path, "rb") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read().decode("ascii"))
    return ctx, fingerprint(der)


def client_context():
    """For the console / tests: encrypted, identity checked by fingerprint instead of a CA."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def peer_fingerprint(ssl_sock) -> str:
    return fingerprint(ssl_sock.getpeercert(binary_form=True))
