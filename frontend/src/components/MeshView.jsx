import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { counts, disposeModel, frame, loadModel, studio } from '../lib/mesh3d'

/* A 3D model (GLB, from the Hub's 3D tools) to turn around in My files' viewer.
 * Like a 360° orbit it spins by itself until it is first grabbed, then stays
 * wherever the hand leaves it: drag turns it, the wheel or a pinch zooms.
 * Loaded on demand (Files.jsx lazy-imports this file), so three.js is only
 * fetched by someone who opens a model. Lit and framed like the model's card
 * (lib/mesh3d.js). */
export default function MeshView({ file, onLoaded }) {
  const box = useRef(null)
  const loaded = useRef(onLoaded)
  const [state, setState] = useState('loading')   // loading | ready | failed
  const [turned, setTurned] = useState(false)

  useEffect(() => { loaded.current = onLoaded }, [onLoaded])

  useEffect(() => {
    const el = box.current
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    el.appendChild(renderer.domElement)
    const { scene, dispose } = studio(renderer)

    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.autoRotate = true
    controls.autoRotateSpeed = 3
    controls.addEventListener('start', () => {
      controls.autoRotate = false
      setTurned(true)
    })

    const fit = () => {
      renderer.setSize(el.clientWidth, el.clientHeight)
      camera.aspect = el.clientWidth / el.clientHeight
      camera.updateProjectionMatrix()
    }
    const resize = new ResizeObserver(fit)
    resize.observe(el)
    fit()

    let raf = requestAnimationFrame(function draw() {
      controls.update()
      renderer.render(scene, camera)
      raf = requestAnimationFrame(draw)
    })

    let model = null
    let closed = false
    loadModel(file.path).then(({ model: loaded3d, radius }) => {
      if (closed) return disposeModel(loaded3d)
      model = loaded3d
      scene.add(model)
      const distance = frame(camera, radius)
      controls.minDistance = radius
      controls.maxDistance = distance * 3
      loaded.current?.(counts(model))
      setState('ready')
    }, () => setState('failed'))

    return () => {
      closed = true
      cancelAnimationFrame(raf)
      resize.disconnect()
      controls.dispose()
      disposeModel(model)
      dispose()
      renderer.dispose()
      el.removeChild(renderer.domElement)
    }
  }, [file.path])

  return (
    // The whole viewer, not a box in it: close up, a box crops the model like
    // a window. The viewer's buttons and details sit on top of it.
    <div onClick={(e) => e.stopPropagation()} className="absolute inset-0">
      <div ref={box} className="absolute inset-0 touch-none cursor-grab active:cursor-grabbing" />
      {state !== 'ready' && (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm font-semibold text-white/80">
          {state === 'loading' ? 'Opening the 3D model…' : 'Could not open this 3D model.'}
        </span>
      )}
      {state === 'ready' && !turned && (
        <span className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 text-sm font-semibold text-white bg-black/60 px-3 py-1.5 rounded-full">
          Drag to turn it
        </span>
      )}
    </div>
  )
}
