"""
Backport of ssl.SSLContext.set_groups() for interpreters that don't have
it yet (native support landed in CPython 3.14, gh-109945 / gh-119244).

Why this exists
----------------
`ciphers=` (-> SSLContext.set_ciphers() -> SSL_CTX_set_cipher_list) only
picks pre-TLS1.3 symmetric cipher suites. It has no effect on which TLS
1.3 key-exchange *group* gets negotiated.
`SSLContext.set_ecdh_curve()` (-> SSL_CTX_set1_groups(ctx, &nid, 1)) only
accepts classical EC curve names recognized as an EC_KEY NID -- a PQC/
hybrid group name registered by oqs-provider (e.g. "X25519MLKEM768",
"mlkem768") has no NID and raises `ssl.SSLError: unknown group`.
The function that actually controls this at the OpenSSL level is the
`SSL_CTX_set1_groups_list()` macro, which forwards an arbitrary string to
`SSL_CTX_ctrl(ctx, SSL_CTRL_SET_GROUPS_LIST, 0, str)` -- no NID lookup,
so any group name a loaded provider has registered works. CPython 3.14's
SSLContext.set_groups() is a one-line wrapper around exactly that call.

This module reproduces that call via ctypes for interpreters older than
3.14, transparently deferring to the native method when it's present.

Verified against CPython 3.11.15 / OpenSSL 3.0.13 (Ubuntu build) with a
real loopback TLS 1.3 handshake -- see selftest() below. IMPORTANT: run
selftest() once on the *actual* target interpreter/OpenSSL build (the
edge node, not just wherever this was written) before trusting it there.
The offset this relies on is not an ABI-stable contract.

How it finds the raw SSL_CTX*
------------------------------
CPython's Modules/_ssl.c defines:
    typedef struct {
        PyObject_HEAD
        SSL_CTX *ctx;
        ...
    } PySSLContext;
and ssl.SSLContext subclasses _ssl._SSLContext directly (no separate
wrapper instance on the versions checked), so `ctx` sits right after
PyObject_HEAD (16 bytes: Py_ssize_t ob_refcnt + PyTypeObject *ob_type on
a 64-bit build). A Python-level subclass with no __slots__ appends its
__dict__ pointer *after* the base type's C fields, so this offset is
unaffected by subclassing.

Preference order if you're choosing how to solve this for real:
  1. CPython >=3.14's native SSLContext.set_groups() -- no ctypes needed.
  2. A locally patched/backported Modules/_ssl.c (the upstream diff is
     ~15 lines, see https://github.com/python/cpython/pull/119244).
  3. This shim, where neither of the above is possible on the deployed
     interpreter.
"""
import ctypes
import ctypes.util
import socket
import ssl
import tempfile
import threading
import os

PyObject_HEAD_SIZE = ctypes.sizeof(ctypes.c_ssize_t) + ctypes.sizeof(ctypes.c_void_p)  # ob_refcnt + ob_type

_libssl_name = ctypes.util.find_library("ssl") or "libssl.so.3"
_libssl = ctypes.CDLL(_libssl_name)

# SSL_CTX_set1_groups_list is a *macro*, not an exported symbol (checked
# with `nm -D libssl.so.3`: absent; checked against /usr/include/openssl/
# ssl.h: `#define SSL_CTX_set1_groups_list(ctx, s) \
#         SSL_CTX_ctrl(ctx, SSL_CTRL_SET_GROUPS_LIST, 0, (char *)(s))`).
# So the ctypes call has to go through the generic SSL_CTX_ctrl() entry
# point with the numeric ctrl code, not a same-named symbol lookup.
SSL_CTRL_SET_GROUPS_LIST = 92  # stable since OpenSSL 1.0.2 (was *_CURVES_LIST pre-3.0 rename)

_libssl.SSL_CTX_ctrl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
_libssl.SSL_CTX_ctrl.restype = ctypes.c_long


def _raw_ssl_ctx_ptr(context: ssl.SSLContext) -> int:
    """Return the SSL_CTX* backing a stdlib ssl.SSLContext, as an int."""
    ctx_field_addr = id(context) + PyObject_HEAD_SIZE
    ctx_ptr = ctypes.cast(ctx_field_addr, ctypes.POINTER(ctypes.c_void_p))[0]
    if not ctx_ptr:
        raise RuntimeError(
            "resolved a NULL SSL_CTX* -- the PyObject_HEAD-offset assumption "
            "this shim relies on doesn't hold for this CPython build. Do not "
            "use this shim here; upgrade to Python >=3.14 (native set_groups) "
            "or backport the CPython patch instead (see module docstring)."
        )
    return ctx_ptr


def set_groups(context: ssl.SSLContext, grouplist: str) -> None:
    """Restrict/order the TLS 1.3 key-exchange groups `context` will
    offer (client) or accept (server) to `grouplist`, an OpenSSL group
    list string -- e.g. "X25519MLKEM768" or a colon-separated preference
    list "X25519MLKEM768:mlkem768:x25519:secp256r1". Works with any group
    name a loaded provider (e.g. oqs-provider) has registered, unlike
    set_ecdh_curve().

    Raises ssl.SSLError if OpenSSL doesn't recognize (one of) the
    group name(s) -- e.g. a PQC group name when oqs-provider isn't
    loaded/configured on this OpenSSL build.
    """
    if hasattr(context, "set_groups"):
        # Native support (CPython >=3.14): use it, skip ctypes entirely.
        context.set_groups(grouplist)
        return
    ctx_ptr = _raw_ssl_ctx_ptr(context)
    buf = ctypes.create_string_buffer(grouplist.encode("ascii"))
    ok = _libssl.SSL_CTX_ctrl(ctx_ptr, SSL_CTRL_SET_GROUPS_LIST, 0, ctypes.cast(buf, ctypes.c_void_p))
    if not ok:
        raise ssl.SSLError(f"unrecognized group(s) in {grouplist!r}")


def selftest(verbose=True):
    """Round-trip proof, on *this* interpreter/OpenSSL build, that
    set_groups() genuinely constrains TLS 1.3 group negotiation and
    isn't silently a no-op.

    Forces a loopback server to accept only group A, then connects two
    *clients* -- mirroring how client.py actually uses this (the edge
    node is the TLS client) -- one forced to a disjoint group B (must
    FAIL with NO_SUITABLE_KEY_SHARE), one forced to group A (must
    SUCCEED). Both legs go through this module's set_groups(), so a
    passing selftest means both server- and client-side usage work on
    this build. Only classical curves are used here (P-256/X25519) so it
    needs no PQC provider to be meaningful -- it validates the *plumbing*
    (ctypes offset, SSL_CTX_ctrl call), not any specific PQC group name.

    Raises AssertionError on any unexpected outcome. Returns True on
    success. Run this once against the actual edge-node interpreter
    before relying on set_groups() there in production.
    """
    group_a, group_b = "P-256", "X25519"

    with tempfile.TemporaryDirectory() as d:
        key_path = os.path.join(d, "key.pem")
        cert_path = os.path.join(d, "cert.pem")
        import subprocess
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key_path,
             "-out", cert_path, "-days", "1", "-nodes", "-subj", "/CN=localhost"],
            check=True, capture_output=True,
        )

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cert_path, key_path)
        server_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        set_groups(server_ctx, group_a)

        host, port = "127.0.0.1", 0
        srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv_sock.bind((host, port))
        srv_sock.listen(2)
        port = srv_sock.getsockname()[1]

        outcomes = []

        def serve_one():
            conn, _ = srv_sock.accept()
            try:
                with server_ctx.wrap_socket(conn, server_side=True) as tls:
                    outcomes.append(("OK", tls.cipher()))
            except ssl.SSLError as e:
                outcomes.append(("FAILED", str(e)))

        def try_client(forced_group):
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.check_hostname = False
            client_ctx.verify_mode = ssl.CERT_NONE
            client_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            set_groups(client_ctx, forced_group)
            with socket.create_connection((host, port), timeout=5) as sock:
                try:
                    with client_ctx.wrap_socket(sock, server_hostname="localhost"):
                        return "OK"
                except ssl.SSLError as e:
                    return f"FAILED: {e}"

        t = threading.Thread(target=serve_one, daemon=True)
        t.start()
        result_b = try_client(group_b)  # disjoint from server's group_a -> must fail
        t.join(timeout=5)
        if verbose:
            print(f"[selftest] client forced to {group_b!r} vs server forced to {group_a!r}: {result_b}")
        assert result_b.startswith("FAILED"), (
            f"expected handshake FAILURE (disjoint groups), got: {result_b}. "
            "set_groups() did not actually restrict negotiation on this build -- "
            "do not trust this shim here."
        )
        assert outcomes and outcomes[-1][0] == "FAILED", outcomes

        t = threading.Thread(target=serve_one, daemon=True)
        t.start()
        result_a = try_client(group_a)  # matches server's forced group -> must succeed
        t.join(timeout=5)
        if verbose:
            print(f"[selftest] client forced to {group_a!r} vs server forced to {group_a!r}: {result_a}")
        assert result_a == "OK", f"expected handshake SUCCESS (matching group), got: {result_a}"
        assert outcomes[-1][0] == "OK", outcomes

        srv_sock.close()

    if verbose:
        print("[selftest] PASSED: set_groups() is genuinely constraining TLS 1.3 group "
              "negotiation on this interpreter/OpenSSL build (client- and server-side).")
    return True


if __name__ == "__main__":
    selftest()