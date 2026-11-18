"""Generate a two-day test CA and loopback-only server certificate in a new folder."""
import argparse
from pathlib import Path
import shutil
import subprocess


def generate(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("Tests require an existing OpenSSL executable")
    commands = [
        ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.crt",
         "-days", "2", "-subj", "/CN=OneBot Isolated Test CA",
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
        ["req", "-newkey", "rsa:2048", "-nodes", "-keyout", "server.key", "-out", "server.csr",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
         "-addext", "basicConstraints=critical,CA:FALSE",
         "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
         "-addext", "extendedKeyUsage=serverAuth"],
        ["x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
         "-CAcreateserial", "-out", "server.crt", "-days", "2", "-copy_extensions", "copy"],
        ["verify", "-CAfile", "ca.crt", "-verify_hostname", "localhost", "server.crt"],
    ]
    for command in commands:
        subprocess.run([openssl, *command], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for path in root.iterdir():
        if path.suffix == ".key":
            path.chmod(0o600)
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    generate(args.directory)
    print("TEST_CERTIFICATES=PASS")
