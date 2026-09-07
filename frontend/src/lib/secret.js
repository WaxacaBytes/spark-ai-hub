/* How this Hub shows a secret it is not hiding from you, only from the room.
 *
 * One rule, one implementation: enough of the key to recognise which one it is,
 * a fixed run of dots so the length leaks nothing, and the last few characters
 * to tell two keys apart. Copy always takes the real value -- masking is about
 * shoulders and screenshots, not about withholding it from its owner.
 */
export function maskKey(key) {
  if (!key) return ''
  if (key.length <= 12) return '•'.repeat(key.length)
  return `${key.slice(0, 8)}${'•'.repeat(24)}${key.slice(-4)}`
}

/* Mask every occurrence of a secret inside a larger blob of text -- a shell
 * one-liner, a Python snippet -- so the block can be shown on screen while the
 * copied value stays the real one. Callers pair this with a Show control. */
export function maskIn(text, secret, shown) {
  if (!text || !secret || shown) return text
  return text.replaceAll(secret, maskKey(secret))
}

/* Does this text carry the secret at all? Drives whether a Show control is
 * worth rendering. */
export function hasSecret(text, secret) {
  return Boolean(text && secret && text.includes(secret))
}
