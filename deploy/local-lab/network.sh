#!/usr/bin/env bash
# Isolated LayerTrace business network on the ZSvirt lab appliance.
set -euo pipefail
task_bridge=br_lab0
task_nic=lab0
task_address=10.50.0.1/24
task_subnet=10.50.0.0/24
task_egress=br_dvs0
task_action=${1:-check}

has_address() {
  ip -4 -o address show dev "$task_bridge" 2>/dev/null | awk '{print $4}' | grep -Fxq "$task_address"
}
has_bridge() {
  ip -o link show type bridge | grep -Eq "^[0-9]+: ${task_bridge}:"
}
has_lab_nic() {
  ip -d -j link show dev "$task_nic" 2>/dev/null | python3 -c '
import json, sys
try:
    links = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(not (len(links) == 1 and links[0].get("linkinfo", {}).get("info_kind") == "dummy"
              and links[0].get("master") in (None, "br_lab0")))'
}
check_rules() {
  iptables -t nat -C POSTROUTING -s "$task_subnet" -o "$task_egress" -j MASQUERADE
  iptables -C FORWARD -i "$task_bridge" -j ACCEPT
  iptables -C FORWARD -o "$task_bridge" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
}

if [[ "$task_action" == apply ]]; then
  if ip link show "$task_bridge" >/dev/null 2>&1; then
    has_bridge || { echo 'br_lab0 exists but is not a bridge' >&2; exit 2; }
  else
    ip link add name "$task_bridge" type bridge
  fi
  # ZSvirt retains lab0 as the L2 physical-interface name across host reboots.
  # The isolated lab uses a dummy interface; recreate it with the bridge.
  if ip link show "$task_nic" >/dev/null 2>&1; then
    has_lab_nic || { echo 'lab0 has an unexpected type or bridge owner' >&2; exit 2; }
  else
    ip link add name "$task_nic" type dummy
  fi
  ip link set "$task_nic" master "$task_bridge"
  ip link set "$task_nic" up
  if ! has_address; then
    if ip -4 -o address show | awk '$2 != "br_lab0" {print $4}' | grep -Fxq "$task_address"; then
      echo '10.50.0.1/24 belongs to another interface' >&2
      exit 2
    fi
    ip address add "$task_address" dev "$task_bridge"
  fi
  ip link set "$task_bridge" up
  sysctl -q -w net.ipv4.ip_forward=1
  iptables -t nat -C POSTROUTING -s "$task_subnet" -o "$task_egress" -j MASQUERADE 2>/dev/null ||
    iptables -t nat -A POSTROUTING -s "$task_subnet" -o "$task_egress" -j MASQUERADE
  iptables -C FORWARD -i "$task_bridge" -j ACCEPT 2>/dev/null ||
    iptables -I FORWARD 1 -i "$task_bridge" -j ACCEPT
  iptables -C FORWARD -o "$task_bridge" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null ||
    iptables -I FORWARD 1 -o "$task_bridge" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
elif [[ "$task_action" != check ]]; then
  echo 'Usage: network.sh check|apply' >&2
  exit 2
fi
has_bridge
has_lab_nic
[[ "$(basename "$(readlink -f /sys/class/net/"$task_nic"/master)")" == "$task_bridge" ]]
ip -o link show dev "$task_nic" | grep -Eq '<([^>]*,)?UP(,|>)'
has_address
[[ "$(sysctl -n net.ipv4.ip_forward)" == 1 ]]
check_rules
echo 'lab0, br_lab0, 10.50.0.1/24 and scoped NAT are ready'
