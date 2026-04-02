import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { VersionSelector } from './VersionSelector'

const t = (key: string) => ({
  'version.label': 'バージョン',
  'version.generating': 'v{n} を生成中',
}[key] ?? key)

describe('VersionSelector', () => {
  it('allows switching to a committed version while a pending version is generating', () => {
    const handleChange = vi.fn()

    render(
      <VersionSelector
        versions={[1, 2]}
        current={1}
        onChange={handleChange}
        t={t}
        pendingVersion={3}
        viewingPending={false}
        onSelectPending={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'v1' }))

    expect(handleChange).toHaveBeenCalledWith(1)
    expect(screen.getByRole('button', { name: 'v3 を生成中' })).toBeInTheDocument()
  })

  it('lets the user return to the live pending workspace', () => {
    const handleSelectPending = vi.fn()

    render(
      <VersionSelector
        versions={[1, 2]}
        current={2}
        onChange={vi.fn()}
        t={t}
        pendingVersion={3}
        viewingPending={false}
        onSelectPending={handleSelectPending}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'v3 を生成中' }))

    expect(handleSelectPending).toHaveBeenCalledTimes(1)
  })
})
