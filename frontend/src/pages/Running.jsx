import { useStore } from '../store'
import RecipeCard from '../components/RecipeCard'

export default function Running() {
  const recipes = useStore((s) => s.recipes)
  const running = recipes.filter((r) => r.running)
  const installed = recipes.filter((r) => r.installed && !r.running)

  return (
    <div className="px-6 py-6 pb-24">
      <h2 className="text-2xl font-extrabold tracking-tight font-[Manrope] mb-1 m-0">
        Running
        <span className="text-text-dim font-normal text-base ml-2">{running.length}</span>
      </h2>
      <p className="text-sm text-text-dim m-0 mb-6">Apps currently active on your system</p>

      {running.length === 0 ? (
        <div className="text-center py-16 text-text-dim animate-fadeIn">
          <div className="text-5xl mb-4">💤</div>
          <div className="text-lg font-semibold font-[Manrope]">No apps are running</div>
          <div className="text-sm mt-1">Install and launch apps from the Catalog</div>
        </div>
      ) : (
        <div className="grid gap-5 mb-10 animate-fadeIn" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>
          {running.map((r) => (
            <RecipeCard key={r.slug} recipe={r} />
          ))}
        </div>
      )}

      {installed.length > 0 && (
        <div className="animate-fadeIn">
          <h2 className="text-2xl font-extrabold tracking-tight font-[Manrope] mb-1 m-0 mt-10">
            Installed
            <span className="text-text-dim font-normal text-base ml-2">{installed.length}</span>
          </h2>
          <p className="text-sm text-text-dim m-0 mb-6">Stopped apps ready to launch</p>
          <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>
            {installed.map((r) => (
              <RecipeCard key={r.slug} recipe={r} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
