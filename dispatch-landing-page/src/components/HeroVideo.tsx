import { useEffect, useState } from 'react';

/* The CSS hero band switches at 760px, so the source has to switch on the same line. */
const WIDE_QUERY = '(min-width: 761px)';

const SOURCES = {
  wide: { src: '/assets/hero-landscape.mp4', poster: '/assets/hero-landscape-poster.jpg' },
  tall: { src: '/assets/hero-portrait.mp4', poster: '/assets/hero-portrait-poster.jpg' },
};

export function HeroVideo() {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE_QUERY).matches);
  const [reduced, setReduced] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );

  useEffect(() => {
    const size = window.matchMedia(WIDE_QUERY);
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onSize = (event: MediaQueryListEvent) => setWide(event.matches);
    const onMotion = (event: MediaQueryListEvent) => setReduced(event.matches);
    size.addEventListener('change', onSize);
    motion.addEventListener('change', onMotion);
    return () => {
      size.removeEventListener('change', onSize);
      motion.removeEventListener('change', onMotion);
    };
  }, []);

  // Reduced motion leaves the drawn stone artwork as the hero instead.
  if (reduced) return null;

  const { src, poster } = wide ? SOURCES.wide : SOURCES.tall;

  return (
    <video
      key={src}
      className="hero-video"
      src={src}
      poster={poster}
      muted
      loop
      autoPlay
      playsInline
      preload="auto"
      tabIndex={-1}
    />
  );
}
