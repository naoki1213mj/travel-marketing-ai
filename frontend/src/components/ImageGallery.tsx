import type { ImageContent } from '../hooks/useSSE'

interface ImageGalleryProps {
  images: ImageContent[]
}

export function ImageGallery({ images }: ImageGalleryProps) {
  if (images.length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
        🖼️ 生成画像
      </h3>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {images.map((img, i) => (
          <div key={i} className="overflow-hidden rounded-lg">
            <img
              src={img.url}
              alt={img.alt}
              className="h-auto w-full object-cover"
            />
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{img.alt}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
