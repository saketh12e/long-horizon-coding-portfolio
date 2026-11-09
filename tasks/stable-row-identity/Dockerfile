FROM rust:1.95.0-slim-trixie@sha256:e14e87345b4d5964ddcc3491d27ee046a0f23820f340c3c1e24da6880141f7c0
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-dev clang lld cmake make pkg-config libssl-dev git patch util-linux procps time ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/python && /opt/python/bin/pip install --no-cache-dir pip==26.2.2 maturin==1.15.0 pyarrow==26.0.0 arro3-core==0.9.1 deprecated==3.0.0 pytest==9.2.0 wrapt==2.5.1 packaging==26.3 iniconfig==2.3.1 pluggy==1.7.0 pygments==2.21.0
ENV PATH="/opt/python/bin:/usr/local/cargo/bin:${PATH}" CARGO_BUILD_JOBS=2 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 RUSTFLAGS="-C linker=clang -C link-arg=-fuse-ld=lld" PYTHONDONTWRITEBYTECODE=1
RUN git init /opt/baseline && git -C /opt/baseline remote add origin https://github.com/delta-io/delta-rs.git && git -C /opt/baseline fetch --depth=1 origin 65e92263a083cd67b5328e9caaf52f1a8467594e && git -C /opt/baseline checkout --detach FETCH_HEAD && test "$(git -C /opt/baseline rev-parse HEAD)" = 65e92263a083cd67b5328e9caaf52f1a8467594e
COPY Cargo.lock /opt/baseline/Cargo.lock
WORKDIR /opt/baseline
RUN cargo fetch --locked && CARGO_TARGET_DIR=/opt/baseline-target CARGO_NET_OFFLINE=true maturin build --manifest-path python/Cargo.toml --locked --offline --out /opt/baseline-wheels --interpreter /opt/python/bin/python
RUN useradd --uid 2000 --create-home candidate && useradd --uid 2001 --create-home inspector && chmod -R a+rX /opt/baseline /usr/local/cargo /usr/local/rustup /opt/python && chmod -R go-w /opt/baseline /usr/local/cargo /usr/local/rustup /opt/python
COPY solution.patch /sample/solution.patch
RUN mkdir -p /app && cp -a /opt/baseline /app/delta-rs && git -C /app/delta-rs apply --check /sample/solution.patch && git -C /app/delta-rs apply /sample/solution.patch
COPY TASK_CONTRACT.md ROW_TRACKING_PROTOCOL.md WORKSPACE.md /app/
COPY tests/ /tests/
RUN mkdir -p /work /logs/verifier /run/controller && chmod 700 /tests /logs/verifier /run/controller && chmod 644 /tests/driver.py /tests/native_probe.rs && chmod 755 /tests/test.sh
WORKDIR /tests
CMD ["/bin/bash", "/tests/test.sh"]
