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
