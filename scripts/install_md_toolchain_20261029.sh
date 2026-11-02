#!/usr/bin/env bash
set -euo pipefail
cd /opt/adhesive-ai-lab
prefix=/opt/adhesive-ai-lab/engines/md-parameterization-20261029/env
bootstrap=/opt/adhesive-ai-lab/engines/md-bootstrap-20261029
mkdir -p "$bootstrap"
cd "$bootstrap"
if [ ! -x bin/micromamba ]; then
    mkdir -p bin
    curl --fail --location --silent --show-error --retry 2 --connect-timeout 20 --max-time 300 https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/linux-64/micromamba-2.10.0-0.tar.bz2 -o micromamba-2.10.0.tar.bz2
    tar -xjf micromamba-2.10.0.tar.bz2 bin/micromamba
    sha256sum micromamba-2.10.0.tar.bz2 bin/micromamba > bootstrap.sha256
fi
export MAMBA_ROOT_PREFIX="$bootstrap/cache"
bin/micromamba create -y -p "$prefix" --override-channels -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge --strict-channel-priority python=3.11 ambertools packmol pyscf geometric
bin/micromamba list -p "$prefix" --explicit > "$bootstrap/environment-explicit.txt"
test -x "$prefix/bin/antechamber"
test -x "$prefix/bin/resp"
test -x "$prefix/bin/packmol"
"$prefix/bin/python" -c 'import pyscf; print("PySCF",pyscf.__version__)'
echo MD_TOOLCHAIN_READY
