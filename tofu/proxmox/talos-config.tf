# The Talos nodes' machine configs (QUEUE-talos.md): what talos/gen-config.py
# builds by hand, built here for the nodes talos.tf creates. Nothing in this
# file touches Proxmox.
#
# The cluster's secrets are TF_VAR_talos_secrets, the base64 of the
# secrets.yaml made from the kubeadm PKI (`talosctl gen secrets
# --from-kubernetes-pki`, the same value as TALOS_SECRETS in Vaultwarden), so
# the Talos control planes share the cluster's CAs. OpenTofu's state holds
# them, and the admin certificate below: the state is as secret as they are.

locals {
  # Talos's version comes from the installer image in talos/patches/common.yaml,
  # which owns it for the hand-built and the OpenTofu-built nodes alike.
  talos_patches_dir = "${path.module}/../../talos/patches"
  talos_common      = file("${local.talos_patches_dir}/common.yaml")
  talos_image_ref   = one(regex("image: factory\\.talos\\.dev/nocloud-installer/([0-9a-f]+:v[0-9.]+)", local.talos_common))
  talos_schematic   = split(":", local.talos_image_ref)[0]
  talos_version     = split(":", local.talos_image_ref)[1]
  talos_contract    = join(".", slice(split(".", local.talos_version), 0, 2))

  # The cluster's Kubernetes version: raised with each upgrade (the kubeadm
  # side through k8s-upgrade.yml, the Talos side through talosctl upgrade-k8s).
  kubernetes_version = "1.36.5"

  # The Talos nodes, by VMID: their role and size here, their host, name and
  # last octet in TF_VAR_site, as for the other guests (README.md). A node is
  # added by a commit of its own, so an apply builds one node (tofu/CLAUDE.md).
  # apply_mode: "auto" while a node is being created (its first config installs
  # Talos and reboots once), then "no_reboot", so a later change that would
  # need a reboot fails the apply instead of rebooting a running node; that
  # change is then made by hand, node by node, after draining.
  # disk_gb: the system disk. A worker's holds every image its pods use, which
  # filled 32G once three workers carried every app (disk pressure evicted
  # pods), so workers get 80; it grows in place, and Talos extends its
  # EPHEMERAL partition at the next boot (proxmox-reboot.yml drains first).
  # data_gb: a worker's second disk, its local-db volume
  # (talos/patches/worker-local-db.yaml); 0 for none.
  talos_nodes = {
    "110" = { role = "controlplane", cores = 2, memory = 4096, disk_gb = 32, data_gb = 0, apply_mode = "no_reboot" }
    "112" = { role = "controlplane", cores = 2, memory = 4096, disk_gb = 32, data_gb = 0, apply_mode = "no_reboot" }
    "113" = { role = "controlplane", cores = 2, memory = 4096, disk_gb = 32, data_gb = 0, apply_mode = "no_reboot" }
    "114" = { role = "worker", cores = 6, memory = 20480, disk_gb = 80, data_gb = 128, apply_mode = "no_reboot" }
    "115" = { role = "worker", cores = 4, memory = 10240, disk_gb = 32, data_gb = 128, apply_mode = "no_reboot" }
    "116" = { role = "worker", cores = 3, memory = 16384, disk_gb = 32, data_gb = 128, apply_mode = "no_reboot" }
    "117" = { role = "worker", cores = 4, memory = 16384, disk_gb = 80, data_gb = 128, apply_mode = "auto" }
  }

  # Each node's address on the main network, the one its kubelet and etcd use.
  talos_net     = var.site.networks["vmbr7"]
  talos_subnet  = "${local.talos_net.prefix}.0/${local.talos_net.prefix_length}"
  talos_address = { for id, _ in local.talos_nodes : id => "${local.talos_net.prefix}.${var.site.guests[id].host}" }

  # secrets.yaml's shape, as the provider names it; the certificates stay in
  # the same base64-wrapped PEM.
  ts = yamldecode(base64decode(var.talos_secrets))
  talos_machine_secrets = {
    cluster    = { id = local.ts.cluster.id, secret = local.ts.cluster.secret }
    trustdinfo = { token = local.ts.trustdinfo.token }
    secrets = merge(
      {
        bootstrap_token             = local.ts.secrets.bootstraptoken
        secretbox_encryption_secret = local.ts.secrets.secretboxencryptionsecret
      },
      try({ aescbc_encryption_secret = local.ts.secrets.aescbcencryptionsecret }, {}),
    )
    certs = {
      etcd               = { cert = local.ts.certs.etcd.crt, key = local.ts.certs.etcd.key }
      k8s                = { cert = local.ts.certs.k8s.crt, key = local.ts.certs.k8s.key }
      k8s_aggregator     = { cert = local.ts.certs.k8saggregator.crt, key = local.ts.certs.k8saggregator.key }
      k8s_serviceaccount = { key = local.ts.certs.k8sserviceaccount.key }
      os                 = { cert = local.ts.certs.os.crt, key = local.ts.certs.os.key }
    }
  }

  # The cluster's own values, as a patch (talos/gen-config.py writes the same
  # from the live cluster).
  talos_site_patch = join("\n---\n", [
    yamlencode({
      apiVersion     = "v1alpha1"
      kind           = "KubeNetworkConfig"
      dnsDomain      = var.site.talos.dns_domain
      podSubnets     = [var.site.talos.pod_subnet]
      serviceSubnets = [var.site.talos.service_subnet]
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "KubeNodeConfig"
      nodeIP     = { validSubnets = [local.talos_subnet] }
    }),
  ])
  talos_etcd_patch = yamlencode({ cluster = { etcd = { advertisedSubnets = [local.talos_subnet] } } })

  # Secrets' encryption at rest, its providers in the order
  # talos/patches/encryption-order.txt gives (which says why it changes in
  # stages); applied after controlplane-encryption-delete.yaml removes the
  # generated document, which a patch would merge into.
  talos_encryption_order = [
    for l in split("\n", file("${local.talos_patches_dir}/encryption-order.txt")) :
    trimspace(l) if trimspace(l) != "" && !startswith(trimspace(l), "#")
  ]
  talos_encryption_provider = {
    identity  = { identity = {} }
    secretbox = { secretbox = { keys = [{ name = "key2", secret = local.ts.secrets.secretboxencryptionsecret }] } }
  }
  talos_encryption_patch = yamlencode({
    apiVersion = "v1alpha1"
    kind       = "KubeEtcdEncryptionConfig"
    config = {
      apiVersion = "apiserver.config.k8s.io/v1"
      kind       = "EncryptionConfiguration"
      resources = [{
        resources = ["secrets"]
        providers = [for p in local.talos_encryption_order : local.talos_encryption_provider[p]]
      }]
    }
  })

  # The API address, held by one control plane at a time (an election through
  # etcd), on the main network's interface: eth0 on these VMs. Not for the
  # talosconfig's endpoints, which must reach a node when etcd or the API
  # server doesn't.
  talos_vip_patch = var.site.talos.api_vip == null ? [] : [yamlencode({
    apiVersion = "v1alpha1"
    kind       = "Layer2VIPConfig"
    name       = var.site.talos.api_vip
    link       = "eth0"
  })]

  talos_patches = {
    controlplane = concat([
      local.talos_common,
      local.talos_site_patch,
      file("${local.talos_patches_dir}/controlplane-encryption-delete.yaml"),
      local.talos_encryption_patch,
      file("${local.talos_patches_dir}/controlplane-podsecurity-delete.yaml"),
      file("${local.talos_patches_dir}/controlplane-podsecurity.yaml"),
      local.talos_etcd_patch,
    ], local.talos_vip_patch)
    worker = [local.talos_common, local.talos_site_patch, file("${local.talos_patches_dir}/worker-local-db.yaml")]
  }
}

# The admin client certificate for Talos's API, signed by the Talos OS CA in
# the secrets (what talosctl gen config puts in a talosconfig). Talos writes
# its Ed25519 keys as "ED25519 PRIVATE KEY"; the body is PKCS#8, which the TLS
# provider reads once the header is the standard one. Renewed a month before
# it expires, on the next apply.
resource "tls_private_key" "talos_admin" {
  algorithm = "ED25519"
}

resource "tls_cert_request" "talos_admin" {
  private_key_pem = tls_private_key.talos_admin.private_key_pem
  subject {
    organization = "os:admin"
  }
}

resource "tls_locally_signed_cert" "talos_admin" {
  cert_request_pem      = tls_cert_request.talos_admin.cert_request_pem
  ca_cert_pem           = base64decode(local.ts.certs.os.crt)
  ca_private_key_pem    = replace(base64decode(local.ts.certs.os.key), "ED25519 PRIVATE KEY", "PRIVATE KEY")
  validity_period_hours = 8760
  early_renewal_hours   = 720
  allowed_uses          = ["digital_signature", "client_auth"]
}

locals {
  talos_client_configuration = {
    ca_certificate     = local.ts.certs.os.crt
    client_certificate = base64encode(tls_locally_signed_cert.talos_admin.cert_pem)
    client_key         = base64encode(tls_private_key.talos_admin.private_key_pem)
  }
}

data "talos_machine_configuration" "node" {
  for_each = local.talos_nodes

  cluster_name       = "kubernetes"
  cluster_endpoint   = "https://${var.site.talos.cluster_endpoint}"
  machine_type       = each.value.role
  machine_secrets    = local.talos_machine_secrets
  talos_version      = local.talos_contract
  kubernetes_version = local.kubernetes_version
  config_patches     = local.talos_patches[each.value.role]
}
