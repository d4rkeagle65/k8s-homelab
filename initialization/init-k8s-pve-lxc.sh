#!/usr/bin/bash
INIT_PVE_HOST=$1
scriptDir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "${scriptDir}/scripts/helper-functions.sh"
source "$(dirname "${scriptDir}")/secrets/pve-lxc-setup.ini"

repoUrl="https://github.com/d4rkeagle65/k8s-homelab.git"
repoDest="/usr/local/k8s-homelab"

declare -A gitInstall_repoClone
gitInstall_repoClone[0]=""
gitInstall_repoClone[1]="apt update"
gitInstall_repoClone[2]="apt install -y git"
gitInstall_repoClone[3]="git clone ${repoUrl} ${repoDest}"

# Get Names of Executing vHost
exeHost=$(hostname)

if ! [ -x "$(command -v pveversion)" ]; then
    echo "Workstation Start"
    gitInstall_repoClone[0]="echo Running on ${INIT_PVE_HOST}"
    gitInstall_repoClone[4]="bash ${repoDest}/initialization/init-k8s-pve-lxc.sh" 
    keyscan_knownhosts $(whoami) "${INIT_PVE_HOST}"
    printf "%s\n" "${gitInstall_repoClone[@]}"
    for (( i=0; i<"${#gitInstall_repoClone[@]}"; i++ )); do
        echo "Command: ${gitInstall_repoClone[$i]}"
        ssh "root@${INIT_PVE_HOST}" "${gitInstall_repoClone[$i]}"
    done
    echo "Workstation End"
else
    echo "PVE Host Start"
    pveHosts=$(pvesh get /nodes --output-format json | jq -r '.[].node')

    for pveHost in $pveHosts; do
        echo "Starting with ${pveHost}"

        # Get the IP Address of the vHost
        pveIP=$(jq -r ".nodelist[\"${pveHost}\"].ip" /etc/pve/.members)

        # Add vHost to Known Hosts To Prevent Prompt
        keyscan_knownhosts "root" "${pveHost}"

        # Clone This Repo to Each vHost and Run vHost Prep Script
        printf "%s\n" "${gitInstall_repoClone[@]}"
        gitInstall_repoClone[0]="echo Running on ${pveHost}"
        gitInstall_repoClone[4]="bash ${repoDest}/initialization/scripts/pve-host-prep.sh"
        for (( i=0; i<"${#gitInstall_repoClone[@]}"; i++ )); do
            echo "Command: ${gitInstall_repoClone[$i]}"
            ssh "root@${pveHost}" "${gitInstall_repoClone[$i]}"
        done
        echo "Done with ${pveHost}"
    done
    echo "PVE Host End"
fi