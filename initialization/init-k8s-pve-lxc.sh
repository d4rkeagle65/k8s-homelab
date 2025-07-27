#!/usr/bin/bash
scriptDir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${scriptDir}/scripts/helper-functions.sh"
source "$(dirname "${scriptDir}")/secrets/pve-lxc-setup.ini"

repoUrl="https://github.com/d4rkeagle65/k8s-homelab.git"
repoDest="/usr/local/k8s-homelab"
gitInstall_repoClone=$(cat << 'EOF'
    apt update
    apt install -y git
    git clone "${repoUrl}" "${repoDest}"
EOF
)

# Get Names of Executing vHost and All vHosts
exeHost=$(hostname)

if ! [ -x "$(command -v pveversion)" ]; then
    copyRun_init="${gitInstall_repoClone}\nbash ${repoDest}/initialization/init-k8s-pve-lxc.sh"
	keyscan_knownhosts "root" "${pveHost}"
    ssh "root@${pveHost}" bash -c $copyRun_init
else
    pveHosts=$(pvesh get /nodes --output-format json | jq -r '.[].node')

    for pveHost in $pveHosts; do
        echo "Starting with ${pveHost}"

        # Get the IP Address of the vHost
        pveIP=$(jq -r ".nodelist[\"${pveHost}\"].ip" /etc/pve/.members)

        # Add vHost to Known Hosts To Prevent Prompt
        keyscan_knownhosts "root" "${pveHost}"

        # Clone This Repo to Each vHost and Run vHost Prep Script
        copyRun_prep="${gitInstall_repoClone}\nbash ${repoDest}/initialization/scripts/pve-host-prep.sh"
        ssh "root@${pveIP}" bash -c $copyRun_prep
        echo "Done with ${pveHost}"
    done
fi

