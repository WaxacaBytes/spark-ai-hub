/* Every URL the Hub prints about itself, derived from the page you are on.
 *
 * The Hub cannot know how it was reached. It might be
 *   http://192.168.3.219:9000        — LAN IP, and the DHCP lease will change it
 *   http://spark-1152.local:9000     — mDNS
 *   http://spark-1152.tail…ts.net    — Tailscale
 *   https://hub.example.com          — a Cloudflare Tunnel, ngrok, someone's nginx
 *
 * In the last case nothing on the server can name that address: it lives in
 * the tunnel provider's config, not on this box. The browser, on the other
 * hand, is holding the answer in `window.location`. So links and copy-paste
 * docs are built from the current page's origin, never from a hostname the
 * daemon guessed or a port someone hardcoded, and they keep working the same
 * whether the reader is on the couch or on the other side of a tunnel.
 */

/** `https://hub.example.com` — scheme, host and port of the page, nothing else. */
export function hubOrigin() {
  return window.location.origin
}

/**
 * Where "Open" points for an app.
 *
 * Proxied apps are served by the Hub itself under /run/{slug}/, so the link is
 * root-relative and needs no host at all — it works over the LAN, Tailscale
 * and a tunnel unchanged. The handful of recipes that still publish a host
 * port of their own (ComfyUI, Open WebUI, Flowise, …) can only be linked
 * host:port; that is reachable when the reader is on the same network as the
 * Spark, and nothing the Hub prints can make it reachable when they are not.
 * The hostname at least follows the page instead of being hardcoded, and the
 * scheme stays http because the container has no certificate.
 */
export function openUrl(recipe) {
  if (recipe.app_url) return recipe.app_url
  return `http://${window.location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`
}

/**
 * Fill the placeholders a recipe's integration fields carry.
 *
 * Recipes ship `<HUB>` and `<API_KEY>` rather than a literal address, because
 * the right address is only known in the browser that is reading them.
 * `<HUB>/v1` is also the only endpoint that survives the trip: the Hub
 * forwards /v1 to whichever model is loaded (daemon/routers/openai_proxy.py),
 * while the model container's own port is published on the LAN and nowhere
 * else, so a tunnel visitor could never reach it. `sah` wires agents up the
 * same way, so what the page documents is what the CLI does.
 *
 * `<SPARK_IP>` is the older placeholder for a direct-to-container URL and is
 * still substituted so an unmigrated recipe renders something sensible.
 */
export function fillPlaceholders(text, { apiKey } = {}) {
  if (!text) return text
  return text
    .replaceAll('<HUB>', hubOrigin())
    .replaceAll('<API_KEY>', apiKey || 'not-needed')
    .replaceAll('<SPARK_IP>', window.location.hostname)
}
