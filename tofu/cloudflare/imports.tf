# The records existed before this module, so the first apply imports each by its
# Cloudflare ids (from TF_VAR_dns). OpenTofu takes import blocks only with keys
# written out, not looped over a variable, hence one per record. Once applied
# they do nothing, and this file can go.

import {
  to = cloudflare_dns_record.this["r01"]
  id = "${var.dns.zones[var.dns.records["r01"].zone]}/${var.dns.records["r01"].id}"
}

import {
  to = cloudflare_dns_record.this["r02"]
  id = "${var.dns.zones[var.dns.records["r02"].zone]}/${var.dns.records["r02"].id}"
}

import {
  to = cloudflare_dns_record.this["r03"]
  id = "${var.dns.zones[var.dns.records["r03"].zone]}/${var.dns.records["r03"].id}"
}

import {
  to = cloudflare_dns_record.this["r04"]
  id = "${var.dns.zones[var.dns.records["r04"].zone]}/${var.dns.records["r04"].id}"
}

import {
  to = cloudflare_dns_record.this["r05"]
  id = "${var.dns.zones[var.dns.records["r05"].zone]}/${var.dns.records["r05"].id}"
}

import {
  to = cloudflare_dns_record.this["r06"]
  id = "${var.dns.zones[var.dns.records["r06"].zone]}/${var.dns.records["r06"].id}"
}

import {
  to = cloudflare_dns_record.this["r07"]
  id = "${var.dns.zones[var.dns.records["r07"].zone]}/${var.dns.records["r07"].id}"
}

import {
  to = cloudflare_dns_record.this["r08"]
  id = "${var.dns.zones[var.dns.records["r08"].zone]}/${var.dns.records["r08"].id}"
}

import {
  to = cloudflare_dns_record.this["r09"]
  id = "${var.dns.zones[var.dns.records["r09"].zone]}/${var.dns.records["r09"].id}"
}

import {
  to = cloudflare_dns_record.this["r10"]
  id = "${var.dns.zones[var.dns.records["r10"].zone]}/${var.dns.records["r10"].id}"
}

import {
  to = cloudflare_dns_record.this["r11"]
  id = "${var.dns.zones[var.dns.records["r11"].zone]}/${var.dns.records["r11"].id}"
}

import {
  to = cloudflare_dns_record.this["r12"]
  id = "${var.dns.zones[var.dns.records["r12"].zone]}/${var.dns.records["r12"].id}"
}

import {
  to = cloudflare_dns_record.this["r13"]
  id = "${var.dns.zones[var.dns.records["r13"].zone]}/${var.dns.records["r13"].id}"
}

import {
  to = cloudflare_dns_record.this["r14"]
  id = "${var.dns.zones[var.dns.records["r14"].zone]}/${var.dns.records["r14"].id}"
}

import {
  to = cloudflare_dns_record.this["r15"]
  id = "${var.dns.zones[var.dns.records["r15"].zone]}/${var.dns.records["r15"].id}"
}

import {
  to = cloudflare_dns_record.this["r16"]
  id = "${var.dns.zones[var.dns.records["r16"].zone]}/${var.dns.records["r16"].id}"
}

import {
  to = cloudflare_dns_record.this["r17"]
  id = "${var.dns.zones[var.dns.records["r17"].zone]}/${var.dns.records["r17"].id}"
}

import {
  to = cloudflare_dns_record.this["r18"]
  id = "${var.dns.zones[var.dns.records["r18"].zone]}/${var.dns.records["r18"].id}"
}

import {
  to = cloudflare_dns_record.this["r19"]
  id = "${var.dns.zones[var.dns.records["r19"].zone]}/${var.dns.records["r19"].id}"
}

import {
  to = cloudflare_dns_record.this["r20"]
  id = "${var.dns.zones[var.dns.records["r20"].zone]}/${var.dns.records["r20"].id}"
}

import {
  to = cloudflare_dns_record.this["r21"]
  id = "${var.dns.zones[var.dns.records["r21"].zone]}/${var.dns.records["r21"].id}"
}

import {
  to = cloudflare_dns_record.this["r22"]
  id = "${var.dns.zones[var.dns.records["r22"].zone]}/${var.dns.records["r22"].id}"
}

import {
  to = cloudflare_dns_record.this["r23"]
  id = "${var.dns.zones[var.dns.records["r23"].zone]}/${var.dns.records["r23"].id}"
}

import {
  to = cloudflare_dns_record.this["r24"]
  id = "${var.dns.zones[var.dns.records["r24"].zone]}/${var.dns.records["r24"].id}"
}

import {
  to = cloudflare_dns_record.this["r25"]
  id = "${var.dns.zones[var.dns.records["r25"].zone]}/${var.dns.records["r25"].id}"
}

import {
  to = cloudflare_dns_record.this["r26"]
  id = "${var.dns.zones[var.dns.records["r26"].zone]}/${var.dns.records["r26"].id}"
}

import {
  to = cloudflare_dns_record.this["r27"]
  id = "${var.dns.zones[var.dns.records["r27"].zone]}/${var.dns.records["r27"].id}"
}

import {
  to = cloudflare_dns_record.this["r28"]
  id = "${var.dns.zones[var.dns.records["r28"].zone]}/${var.dns.records["r28"].id}"
}

import {
  to = cloudflare_dns_record.this["r29"]
  id = "${var.dns.zones[var.dns.records["r29"].zone]}/${var.dns.records["r29"].id}"
}
