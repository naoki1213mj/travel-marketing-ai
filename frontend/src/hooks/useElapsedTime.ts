import { useState, useEffect, useRef } from 'react'

export function useElapsedTime(isRunning: boolean, resetKey: number = 0): number {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef<number>(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (isRunning) {
      startRef.current = Date.now()
      setElapsed(0)
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startRef.current) / 1000))
      }, 1000)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [isRunning, resetKey])

  return elapsed
}
