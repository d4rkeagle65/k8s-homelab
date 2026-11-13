# grocy-mealie-sync

[HarmEllis/grocy-mealie-sync](https://github.com/HarmEllis/grocy-mealie-sync) (MIT): a
bidirectional bridge between Grocy's stock and Mealie's shopping list, run as a Dockhand
Git stack on the Docker host. It links the two apps' **shopping list and stock only** — it
does not sync recipes (Grocy's own "cook from stock" uses recipes entered in Grocy).

How it syncs (products and units matched by name):

- Grocy stock below a product's **minimum** -> the product is added to the Mealie shopping list.
- Checking an item off in Mealie -> a purchase is booked into Grocy's stock and the item is
  cleared from Grocy's own shopping list. (Un-checking is ignored.)

It reaches both apps over their LAN ingress URLs: `grocy.${BASE_DOMAIN}` (via Grocy's `/api`,
the one route left outside Authentik forward-auth, with a Grocy API key) and
`mealie.${BASE_DOMAIN}` (with a Mealie API token). Its own web UI — the product-mapping
wizard — is on port 3000, published on the host's main-network address and loopback, behind
its own login (`AUTH_SECRET`). State is a small SQLite file in a named volume.

Tested upstream with Grocy 4.x and Mealie 3.12+ (shared-unit standardization needs Mealie
3.13+).

## Setup (owner)

1. **Grocy API key**: Grocy -> Settings -> Manage API keys -> Add.
2. **Mealie API token**: Mealie -> User Settings -> API Tokens -> Create.
3. **Auth secret**: `openssl rand -base64 32`.
4. **Key Vault** (kv-hsn-1) — add three secrets:
   - `GROCY-API-KEY` = the Grocy key from step 1
   - `MEALIE-API-TOKEN` = the Mealie token from step 2
   - `GROCY-MEALIE-SYNC-AUTH-SECRET` = the value from step 3
5. In **Grocy**, set a positive minimum stock amount on the products that should appear on
   the Mealie list when they run low (nothing syncs out of Grocy without one).
6. Add this folder as a **Git stack in Dockhand**, Key Vault bound as its secret provider
   (selector empty). Then add its webhook to
   `kubernetes/prod/system/external-services/dockhand-webhooks/app/webhooks.yaml` using the
   new stack's Dockhand ID (a Provider + Alert, like the others), so `main` moves redeploy it.
7. Deploy, then open `http://<docker-host>:3000`, log in, and run the product-mapping wizard.

Renovate moves the image tag; Dockhand's auto-update stays off.
