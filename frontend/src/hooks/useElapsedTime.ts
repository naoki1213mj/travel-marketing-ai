import { useEffect, useRef, useState } from 'react'

export function useElapsedTime(isRunning: boolean, resetKey: number = 0): number {
  const [elapsedState, setElapsedState] = useState({
    value: 0,
    isRunning,
    resetKey,
  })
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (!isRunning) {
      return () => undefined
    }

    const startTime = Date.now()
    timerRef.current = setInterval(() => {
      setElapsedState({
        value: Math.floor((Date.now() - startTime) / 1000),
        isRunning,
        resetKey,
      })
    }, 1000)

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [isRunning, resetKey])

  if (!isRunning) {
    return 0
  }

  if (elapsedState.isRunning !== isRunning || elapsedState.resetKey !== resetKey) {
    return 0
  }

  return elapsedState.value
}
