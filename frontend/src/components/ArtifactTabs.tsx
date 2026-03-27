import { useState, type ReactNode } from 'react'

interface Tab {
  key: string
  label: string
  content: ReactNode
}

interface ArtifactTabsProps {
  tabs: Tab[]
}

export function ArtifactTabs({ tabs }: ArtifactTabsProps) {
  const [activeTab, setActiveTab] = useState(tabs[0]?.key || '')

  const activeTabs = tabs.filter(tab => tab.content !== null)
  if (activeTabs.length === 0) return null

  return (
    <div>
      <div className="flex border-b border-gray-200 dark:border-gray-700">
        {activeTabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors
              ${activeTab === tab.key
                ? 'border-b-2 border-blue-500 text-blue-600 dark:text-blue-400'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300'
              }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="py-4">
        {activeTabs.find(tab => tab.key === activeTab)?.content}
      </div>
    </div>
  )
}
