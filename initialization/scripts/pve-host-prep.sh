#!/usr/bin/bash
scriptDir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Modules
# Configure and Load for k8s
modulesFile="/etc/modules-load.d/k8s-modules.conf"
cp "$(dirname "${scriptDir}")/configs/k8s-modules.conf" $modulesFile
mapfile -t modules < <(grep -v '^\s*$' "$modulesFile")
modprobe -a "${modules[@]}"
update-initramfs -u

# Sysctl
# Setup for IP Forwarding, Network Bridges and nf_conntrack_max
# k8s expects nf_contrack_max to be 65536 for each CPU available core,
# On VMs this would not be required, but LXCs cannot update the value.
nf_conntrack_max=$((65536*$(cat /proc/cpuinfo | grep processor | wc -l)))
sysctlFile="/etc/sysctl.d/k8s-sysctl.conf"
cp "$(dirname "${scriptDir}")/k8s-sysctl.conf" $sysctlFile
echo "net.netfilter.nf_conntrack_max      = ${nfconntrack_max}" >> $sysctlFile
sysctl --system

# Disable Swap
swapoff -a
sed -i '/ swap / s/^\(.*\)$/#\1/g' /etc/fstab

# Download Debian 12 Template
source <(grep '=' "$(dirname "${scriptDir}")/configs/k8s-lxc-template.ini" | sed 's/ *= */=/g' | sed 's/;/#/g')
pveam download $DEST_STORAGE_ID $LXC_TEMPLATE