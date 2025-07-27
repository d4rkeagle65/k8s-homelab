#!/usr/bin/bash

function keyscan_knownhosts {
    user=$1
    target=$2

    if [[ $user -eq 'root' ]]; then
        userpath="/root"
    else
        userpath="/home/${user}"
    fi

    knownHostsFile="${userpath}/.ssh/known_hosts"
    if [ ! -e "$knownHostsFile" ]; then touch $knownHostsFile; fi
    if [[ $(cat ${knownHostsFile} | grep ${target} | wc -l) -eq 0 ]]; then
        ssh-keyscan -t rsa ${target} >> ${userpath}/.ssh/known_hosts
    fi
}