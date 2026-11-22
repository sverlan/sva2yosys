# The image of the continuous-integration jobs that need external tools: slim Debian with Python,
# g++ and make (for the Verilator probes), git, curl and uv (the stage "base", the only base image
# of every stage), plus, from several build stages that leave no download or compiler output
# behind,
#   - one pinned release of the OSS CAD Suite (https://github.com/YosysHQ/oss-cad-suite-build:
#     Yosys, SBY with its suprove liveness engine, solvers, Verilator), without the FPGA place and
#     route tools, the VHDL and Verilog simulators, the waveform viewers and the other parts the
#     tests never run;
#   - EBMC 6.1 (BSD-3-Clause), Spot 2.16.1 (GPL-3.0; compiled here from its source, only its
#     ltl2tgba program is kept, run as an external program) and the slang executable of the same
#     version as pyslang (MIT): the opt-in tools of the differential and exporter tests.
# The suite and the tools are collections of free software under their own licences.
#
# Build and publish (the values of the three suite arguments are the ones of
# .github/workflows/ci.yml; a test keeps them equal):
#
#   docker build -t <namespace>/sva2yosys-ci:2026-10-31 docker/ci
#   docker push <namespace>/sva2yosys-ci:2026-10-31
#
# The tag of the image is the date of the OSS CAD Suite release. To change the suite, change the
# three arguments (the release tag, the file name and its SHA-256, shown with the asset on the
# release page) here and in ci.yml, and build again. The other tools have their own arguments
# below; every download is checked against a SHA-256 before it is used. Compiling Spot takes
# about twenty minutes.
ARG BASE=python:3.14-slim-trixie


# The base of every stage: slim Debian 13 with Python 3.14, the compiler (Verilator builds the
# probes with it, Spot is compiled with it), make, git (the corpora are cloned), curl and the
# latest uv.
FROM ${BASE} AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ make git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/


# The OSS CAD Suite, pruned.
FROM base AS suite

ARG OSS_CAD_TAG=2026-10-31
ARG OSS_CAD_FILE=oss-cad-suite-linux-x64-20261031.tgz
ARG OSS_CAD_SHA256=e0349f66547ca1ac9a24b5e06287d4719aa0213d431e1168eb319915340dc665

RUN set -eux; \
    url="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_TAG}/${OSS_CAD_FILE}"; \
    curl -fsSL -o "/tmp/${OSS_CAD_FILE}" "${url}"; \
    echo "${OSS_CAD_SHA256}  /tmp/${OSS_CAD_FILE}" | sha256sum -c -; \
    tar -xzf "/tmp/${OSS_CAD_FILE}" -C /opt; \
    rm "/tmp/${OSS_CAD_FILE}"

# Remove what the tests never run (2.5 GB become 1 GB): place and route (nextpnr, icestorm, trellis,
# prjoxide, apicula and the programmers), the VHDL and Verilog simulators (ghdl, iverilog), the
# waveform viewers (gtkwave, surfer) and the graphical libraries they bring (LLVM, Mesa, Qt, GTK).
# The suite's own Python and the solvers stay, without its test suites, IDLE, Tk, pip, the
# FPGA and testbench packages (cocotb, apycula, amaranth, migen) and the debug Verilator.
RUN set -eux; \
    cd /opt/oss-cad-suite; \
    rm -rf examples; \
    rm -rf libexec/nextpnr-* libexec/prjoxide libexec/surfer libexec/surver libexec/ivl \
           libexec/vhdlpp libexec/ghdl libexec/ghdl1-llvm libexec/gtkwave libexec/openocd \
           libexec/openFPGALoader libexec/glycin-* libexec/mcy-gui libexec/sby-gui libexec/ecpdap; \
    rm -rf share/nextpnr share/icebox share/trellis share/openFPGALoader share/openocd \
           share/gtkwave3 share/icons share/fonts share/mcy; \
    rm -rf lib/libLLVM* lib/libgallium* lib/ghdl lib/libghdl* lib/ivl lib/libvvp* lib/libgtk-* \
           lib/libQt6* lib/qt6 lib/libglycin* lib/librsvg* lib/libgnat*; \
    rm -rf libexec/verilator_bin_dbg; \
    for py in lib/python3.11 lib/python2.7; do \
        rm -rf $py/test $py/idlelib $py/tkinter $py/turtledemo $py/lib-tk $py/lib2to3 $py/ensurepip; \
    done; \
    rm -rf lib/python3.11/site-packages/pip lib/python3.11/site-packages/pip-* \
           lib/python3.11/site-packages/cocotb* lib/python3.11/site-packages/apycula* \
           lib/python3.11/site-packages/amaranth* lib/python3.11/site-packages/migen*; \
    cd bin; \
    rm -f nextpnr-* prjoxide surfer surver iverilog iverilog-vpi vvp ghdl ghdl1-llvm ghwdump \
          gtkwave openocd openFPGALoader sby-gui mcy mcy-dash mcy-gui ecp* ice* icebox_* gowin_* \
          fujprog dfu-* lsusb lsftdi vhdlpp rtlbrowse twinwave xdot; \
    cd ..; \
    ./bin/yosys -V; \
    ./bin/sby --help | head -n 1; \
    ./bin/verilator --version; \
    ./bin/suprove --help 2>&1 | head -n 1 || true; \
    for solver in yices-smt2 z3 boolector bitwuzla cvc5 yosys-abc; do ./bin/$solver --version | head -n 1 || true; done; \
    du -sm /opt/oss-cad-suite


# EBMC and the slang executable.
FROM base AS downloads

ARG EBMC_URL=https://github.com/diffblue/hw-cbmc/releases/download/ebmc-6.1/ebmc_6.1_amd64.deb
ARG EBMC_SHA256=aa0d3e6ab04efcd85c270fd3f192bbcbc18fdc03bda2f21832a481c28a32da0c
ARG SLANG_URL=https://github.com/MikePopoloski/slang/releases/download/v12.0/slang-linux-x86_64.tar.gz
ARG SLANG_SHA256=d1f680e2c90b95daab3b812a6431055e3d8828fde281dbf89b72ee3eceb90dd5

RUN set -eux; \
    curl -fsSL -o /tmp/ebmc.deb "${EBMC_URL}"; \
    echo "${EBMC_SHA256}  /tmp/ebmc.deb" | sha256sum -c -; \
    mkdir /tmp/ebmc; \
    dpkg-deb -x /tmp/ebmc.deb /tmp/ebmc; \
    mkdir -p /out/ebmc; \
    cp "$(find /tmp/ebmc -type f -name ebmc | head -n 1)" /out/ebmc/ebmc; \
    /out/ebmc/ebmc --version; \
    curl -fsSL -o /tmp/slang.tar.gz "${SLANG_URL}"; \
    echo "${SLANG_SHA256}  /tmp/slang.tar.gz" | sha256sum -c -; \
    mkdir /tmp/slang; \
    tar -xzf /tmp/slang.tar.gz -C /tmp/slang; \
    mkdir -p /out/slang; \
    cp "$(find /tmp/slang -type f -name slang | head -n 1)" /out/slang/slang; \
    /out/slang/slang --version


# Spot, compiled from its source; only ltl2tgba is kept.
FROM base AS spot

ARG SPOT_URL=https://www.lre.epita.fr/dload/spot/spot-2.16.1.tar.gz
# Computed when the tarball was first downloaded (2026-11-01): the project publishes no checksum.
ARG SPOT_SHA256=182f4180da851588886a2e5f1540c3e2bb5cfddd228fd20689fa7ce498f57733

RUN set -eux; \
    curl -fsSL -o /tmp/spot.tar.gz "${SPOT_URL}"; \
    echo "${SPOT_SHA256}  /tmp/spot.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/spot.tar.gz -C /tmp; \
    cd /tmp/spot-*/; \
    ./configure --prefix=/tmp/spot-install --disable-python --disable-doxygen \
                --disable-shared --enable-static CXXFLAGS="-O2 -g0"; \
    make -j"$(nproc)"; \
    mkdir -p /out/spot; \
    cp bin/ltl2tgba /out/spot/ltl2tgba; \
    strip /out/spot/ltl2tgba; \
    /out/spot/ltl2tgba -f 'a U b' | head -n 3


# The image of the jobs.
FROM base

LABEL org.opencontainers.image.title="sva2yosys-ci" \
      org.opencontainers.image.description="Python, uv, the OSS CAD Suite (Yosys, SBY, Verilator), EBMC, Spot and slang for the sva2yosys tests" \
      org.opencontainers.image.source="https://github.com/sverlan/sva2yosys" \
      org.opencontainers.image.licenses="MIT"

COPY --from=suite /opt/oss-cad-suite /opt/oss-cad-suite
COPY --from=downloads /out/ebmc /opt/tools/ebmc
COPY --from=downloads /out/slang /opt/tools/slang
COPY --from=spot /out/spot /opt/tools/spot

# Where the tests look for the tools (tests/support/toolchain.py; nothing is searched on PATH).
# SVA2YOSYS_LTL2TGBA is the directory that contains ltl2tgba. The liveness engine is the one of the
# suite's sby. The Verilator build runs with a PATH of only the directories listed in
# SVA2YOSYS_VERILATOR_BUILD_PATH: /usr/bin for g++ and make, /usr/local/bin for python3.
ENV SVA2YOSYS_YOSYS=/opt/oss-cad-suite/bin/yosys \
    SVA2YOSYS_SBY=/opt/oss-cad-suite/bin/sby \
    SVA2YOSYS_SBY_LIVE=/opt/oss-cad-suite/bin/sby \
    SVA2YOSYS_EBMC=/opt/tools/ebmc/ebmc \
    SVA2YOSYS_LTL2TGBA=/opt/tools/spot \
    SVA2YOSYS_SLANG=/opt/tools/slang/slang \
    SVA2YOSYS_VERILATOR=/opt/oss-cad-suite/bin/verilator_bin \
    SVA2YOSYS_VERILATOR_SHELL=/bin/sh \
    SVA2YOSYS_VERILATOR_ROOT=/opt/oss-cad-suite/share/verilator \
    SVA2YOSYS_VERILATOR_BUILD_PATH=/usr/bin:/usr/local/bin
