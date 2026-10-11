"""S1 black-box CLI flags (additive only; no fixtures, no collection changes)."""


def pytest_addoption(parser):
    parser.addoption("--native-run", action="store_true", default=False,
                     help="run the S1 live-server subset against --native-url")
    parser.addoption("--native-url", default="",
                     help="live trace-lite server base URL (e.g. http://127.0.0.1:8471)")
    parser.addoption("--native-db", default="",
                     help="optional on-disk DB path for byte-truth cross-checks")
