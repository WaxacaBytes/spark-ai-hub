import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

/* A 360° picture or clip (an equirectangular frame: the whole sphere around
 * the camera unwrapped, straight ahead in the middle, behind at both edges)
 * seen from inside, in My files' viewer. Drag looks around, the wheel or a
 * pinch zooms. A clip plays with its sound and loops; the button pauses it.
 * Loaded on demand (Files.jsx lazy-imports this file), like MeshView. */
export default function SphereView({ file }) {
  const box = useRef(null)
  const clip = useRef(null)
  const [state, setState] = useState('loading')   // loading | ready | failed
  const [turned, setTurned] = useState(false)
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    const el = box.current
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    el.appendChild(renderer.domElement)
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 100)

    let video = null
    let texture
    if (file.kind === 'video') {
      video = document.createElement('video')
      Object.assign(video, { src: file.path, loop: true, playsInline: true })
      video.addEventListener('loadeddata', () => setState('ready'), { once: true })
      video.addEventListener('error', () => setState('failed'), { once: true })
      // Opened by a click, so sound is allowed; a browser that still refuses
      // plays it muted.
      video.play().catch(() => { video.muted = true; video.play().catch(() => {}) })
      clip.current = video
      texture = new THREE.VideoTexture(video)
    } else {
      texture = new THREE.TextureLoader().load(file.path, () => setState('ready'), undefined, () => setState('failed'))
    }
    texture.colorSpace = THREE.SRGBColorSpace
    const geometry = new THREE.SphereGeometry(10, 96, 48)
    geometry.scale(-1, 1, 1)                     // its inside faces the camera
    const material = new THREE.MeshBasicMaterial({ map: texture })
    scene.add(new THREE.Mesh(geometry, material))

    // Where the camera looks, in degrees: the frame's middle (straight ahead)
    // sits at longitude 270 on this sphere; the longitude falls as the view
    // turns right.
    const look = { lon: 270, lat: 0 }
    const pointers = new Map()
    let pinch = 0
    const canvas = renderer.domElement
    const down = (e) => {
      canvas.setPointerCapture(e.pointerId)
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
      setTurned(true)
    }
    const move = (e) => {
      const last = pointers.get(e.pointerId)
      if (!last) return
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()]
        const span = Math.hypot(a.x - b.x, a.y - b.y)
        if (pinch) zoom((pinch - span) * 0.15)
        pinch = span
        return
      }
      // The scene follows the hand (dragging right brings in what is on the
      // left), slower when zoomed in.
      const speed = 0.12 * (camera.fov / 75)
      look.lon += (e.clientX - last.x) * speed
      look.lat = Math.max(-85, Math.min(85, look.lat + (e.clientY - last.y) * speed))
    }
    const up = (e) => {
      pointers.delete(e.pointerId)
      if (pointers.size < 2) pinch = 0
    }
    const zoom = (d) => {
      camera.fov = Math.max(30, Math.min(100, camera.fov + d))
      camera.updateProjectionMatrix()
    }
    const wheel = (e) => {
      e.preventDefault()
      zoom(e.deltaY * 0.05)
    }
    canvas.addEventListener('pointerdown', down)
    canvas.addEventListener('pointermove', move)
    canvas.addEventListener('pointerup', up)
    canvas.addEventListener('pointercancel', up)
    canvas.addEventListener('wheel', wheel, { passive: false })

    const fit = () => {
      renderer.setSize(el.clientWidth, el.clientHeight)
      camera.aspect = el.clientWidth / el.clientHeight
      camera.updateProjectionMatrix()
    }
    const resize = new ResizeObserver(fit)
    resize.observe(el)
    fit()

    const target = new THREE.Vector3()
    let raf = requestAnimationFrame(function draw() {
      const phi = THREE.MathUtils.degToRad(90 - look.lat)
      const theta = THREE.MathUtils.degToRad(look.lon)
      target.setFromSphericalCoords(1, phi, theta)
      camera.lookAt(target)
      renderer.render(scene, camera)
      raf = requestAnimationFrame(draw)
    })

    return () => {
      cancelAnimationFrame(raf)
      resize.disconnect()
      canvas.removeEventListener('wheel', wheel)
      if (video) {
        video.pause()
        video.removeAttribute('src')
        video.load()
      }
      clip.current = null
      texture.dispose()
      material.dispose()
      geometry.dispose()
      renderer.dispose()
      el.removeChild(canvas)
    }
  }, [file.path, file.kind])

  const playPause = () => {
    const video = clip.current
    if (!video) return
    if (video.paused) video.play()
    else video.pause()
    setPaused(video.paused)
  }

  return (
    // The whole viewer: the sphere is all around, so no frame crops it. The
    // viewer's buttons and details sit on top of it.
    <div onClick={(e) => e.stopPropagation()} className="absolute inset-0">
      <div ref={box} className="absolute inset-0 touch-none cursor-grab active:cursor-grabbing" />
      {state !== 'ready' && (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm font-semibold text-white/80">
          {state === 'loading' ? 'Opening the 360° view…' : 'Could not open this file.'}
        </span>
      )}
      {state === 'ready' && !turned && (
        <span className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 text-sm font-semibold text-white bg-black/60 px-3 py-1.5 rounded-full">
          Drag to look around
        </span>
      )}
      {state === 'ready' && file.kind === 'video' && (
        <button
          onClick={playPause}
          className="absolute bottom-4 right-4 h-11 px-4 rounded-full bg-black/55 text-white text-sm font-semibold backdrop-blur-sm border-none cursor-pointer hover:bg-black/80 transition-colors"
        >
          {paused ? 'Play' : 'Pause'}
        </button>
      )}
    </div>
  )
}
