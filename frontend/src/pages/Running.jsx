import { useStore } from '../store'
import RecipeCard from '../components/RecipeCard'

export default function Running() {
  const recipes = useStore((s) => s.recipes)
  const running = recipes.filter((r) => r.running)
  const installed = recipes.filter((r) => r.installed && !r.running)

  return (
    <div className="px-6 py-4">
      <h2 className="text-lg font-bold mb-4">
        Running <span className="text-text-dim font-normal text-sm">({running.length})</span>
      </h2>
      {running.length === 0 ? (
        <div className="text-center py-12 text-text-dim">
          <div className="text-4xl mb-3">💤</div>
          <div>No apps are currently running</div>
        </div>
      ) : (
        <div className="grid gap-4 mb-8" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))' }}>
          {running.map((r) => (
            <RecipeCard key={r.slug} recipe={r} />
          ))}
        </div>
      )}

      {installed.length > 0 && (
        <>
          <h2 className="text-lg font-bold mb-4 mt-8">
            Installed (Stopped) <span className="text-text-dim font-normal text-sm">({installed.length})</span>
          </h2>
          <div className="grid gap-4 pb-60" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))' }}>
            {installed.map((r) => (
              <RecipeCard key={r.slug} recipe={r} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
