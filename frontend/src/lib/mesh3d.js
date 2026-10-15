import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'

/* How the Hub shows a 3D model (GLB), shared by My files' viewer
 * (components/MeshView.jsx) and the still on a model's card (snapshot below),
 * so a card looks like the model it opens. Imported on demand only: three.js
 * is fetched by someone who has a 3D model to see. */

// Soft studio light from every side, so a painted (unlit) texture reads as it
// was painted rather than as a dark silhouette.
export function studio(renderer) {
  renderer.toneMapping = THREE.NeutralToneMapping
  const scene = new THREE.Scene()
  const pmrem = new THREE.PMREMGenerator(renderer)
  const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
  scene.environment = environment
  return { scene, dispose: () => { environment.dispose(); pmrem.dispose() } }
}

// The model, centred on the origin, and the radius that encloses it.
export async function loadModel(url) {
  const model = (await new GLTFLoader().loadAsync(url)).scene
  const bounds = new THREE.Box3().setFromObject(model)
  model.position.sub(bounds.getCenter(new THREE.Vector3()))
  return { model, radius: bounds.getBoundingSphere(new THREE.Sphere()).radius || 1 }
}

// Back off until the whole model fits the narrower of the two fields of view
// (a phone held upright is far narrower than it is tall), a little above it
// and turned `yaw` radians round from its front. Returns the distance.
export function frame(camera, radius, yaw = 0) {
  const fov = THREE.MathUtils.degToRad(camera.fov)
  const across = 2 * Math.atan(Math.tan(fov / 2) * camera.aspect)
  const distance = (radius / Math.sin(Math.min(fov, across) / 2)) * 1.1
  camera.near = radius / 100
  camera.far = distance * 10
  camera.position.set(Math.sin(yaw) * distance, distance * 0.2, Math.cos(yaw) * distance)
  camera.lookAt(0, 0, 0)
  camera.updateProjectionMatrix()
  return distance
}

export function counts(model) {
  let vertices = 0
  let triangles = 0
  model.traverse((o) => {
    if (!o.isMesh) return
    vertices += o.geometry.attributes.position.count
    triangles += (o.geometry.index?.count ?? o.geometry.attributes.position.count) / 3
  })
  return { vertices, triangles }
}

export function disposeModel(model) {
  model?.traverse((o) => {
    o.geometry?.dispose()
    for (const m of [].concat(o.material || [])) {
      for (const v of Object.values(m)) if (v?.isTexture) v.dispose()
      m.dispose()
    }
  })
}

/* One still of the model for its card: a PNG with a transparent background,
 * from the view the viewer opens on, cropped to the model so it fills the
 * card. The Hub's daemon has no GPU renderer, so the browser that lists the
 * model draws this and hands it back (Files.jsx), and every later visit shows
 * it. */
export async function snapshot(url, size = 768) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })
  renderer.setSize(size, size, false)
  const { scene, dispose } = studio(renderer)
  let model = null
  try {
    let radius
    ;({ model, radius } = await loadModel(url))
    scene.add(model)
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100)
    frame(camera, radius)
    renderer.render(scene, camera)
    const still = cropToContent(renderer.domElement)
    return await new Promise((ok, fail) => still.toBlob(
      (png) => (png ? ok(png) : fail(new Error('The browser could not draw the model.'))), 'image/png'))
  } finally {
    disposeModel(model)
    dispose()
    renderer.dispose()
    renderer.forceContextLoss()
  }
}

// The square around what was drawn, with a little room: framing has to allow
// for any shape, so a model alone fills only part of its frame.
function cropToContent(canvas, margin = 0.08) {
  const { width, height } = canvas
  const flat = document.createElement('canvas')
  flat.width = width
  flat.height = height
  const ctx = flat.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(canvas, 0, 0)
  const alpha = ctx.getImageData(0, 0, width, height).data
  let left = width, top = height, right = -1, bottom = -1
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (alpha[(y * width + x) * 4 + 3] > 8) {
        if (x < left) left = x
        if (x > right) right = x
        if (y < top) top = y
        if (y > bottom) bottom = y
      }
    }
  }
  if (right < 0) return canvas
  const side = Math.ceil(Math.max(right - left, bottom - top) * (1 + 2 * margin))
  const out = document.createElement('canvas')
  out.width = side
  out.height = side
  out.getContext('2d').drawImage(flat, (left + right - side) / 2, (top + bottom - side) / 2, side, side, 0, 0, side, side)
  return out
}
