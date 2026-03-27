interface VersionSelectorProps {
  versions: number[]
  current: number
  onChange: (version: number) => void
}

export function VersionSelector({ versions, current, onChange }: VersionSelectorProps) {
  if (versions.length <= 1) return null

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 dark:text-gray-400">バージョン:</span>
      <div className="flex gap-1">
        {versions.map(v => (
          <button
            key={v}
            onClick={() => onChange(v)}
            className={`rounded px-2 py-0.5 text-xs
              ${v === current
                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-400'
              }`}
          >
            v{v}
          </button>
        ))}
      </div>
    </div>
  )
}
