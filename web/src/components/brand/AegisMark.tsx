/**
 * The Aegis brand mark — a falcon with raised wings.
 *
 * The shape is traced from the artwork the owner supplied
 * (`web/public/brand/falcon-source.jpg`, kept in the repo as the source of
 * truth). Tracing was done with a threshold -> Moore boundary trace -> Chaikin
 * smoothing -> Douglas-Peucker simplify pass, so this is a real vector: crisp at
 * any size, one path, no raster dependency at runtime.
 *
 * **Why the viewBox is not square.** The mark is roughly 1.6:1 wide. Letterboxed
 * into a 64x64 box it shrinks to a smudge inside the app's square logo tile, so
 * the viewBox keeps the artwork's own aspect and callers size it by `width` with
 * the height following. Inside a square tile, set `width` to about 60-65% of the
 * tile so the wingspan reads.
 *
 * Filled with `currentColor`: white on the dark tile in the sidebar and login,
 * ink-on-white in the landing footer.
 *
 * Decorative by default (`aria-hidden`) since every current use sits beside the
 * "Aegis" wordmark. Pass `title` when the mark stands alone.
 */

/** Intrinsic aspect ratio of the traced artwork (width / height). */
export const AEGIS_MARK_RATIO = 218 / 136

export interface AegisMarkProps {
  /** Rendered width in px; height follows the artwork's aspect ratio. */
  width?: number
  className?: string
  /** Accessible name. Omit for decorative use beside the wordmark. */
  title?: string
}

/** The falcon. See the module docstring for sizing guidance. */
export function AegisMark({ width = 22, className, title }: AegisMarkProps) {
  return (
    <svg
      width={width}
      height={width / AEGIS_MARK_RATIO}
      viewBox="0 0 218 136"
      fill="currentColor"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      <path
        fillRule="evenodd"
        d="
          M0.44 0.44L13.06 7.16L14.94 8.84L29.19 14.95L41.19 18.95L43.69 19.02L45.19 19.95L47.69
          20.02L49.19 20.95L51.69 21.02L57.31 22.98L61.69 23.02L63.19 23.95L66.69 24.02L68.31
          24.98L74.06 25.16L75.95 26.23L77.09 29.94L82.33 39.31L86.69 43.67L93.31 47.98L94.81
          47.95L95.84 47.06L96.95 44.81L98.09 39.06L101.44 32.56L106.06 28.09L109.31 27.02L113.94
          27.09L118.56 29.56L121.00 34.44L120.84 36.91L118.31 36.02L116.09 38.06L116.09 44.94L118.06
          47.91L123.56 48.00L130.31 43.67L134.67 39.31L139.91 29.94L141.05 26.23L142.94 25.16L148.69
          24.98L150.19 24.05L153.69 23.98L155.31 23.02L159.69 22.98L161.19 22.05L163.69 21.98L165.19
          21.05L175.81 18.95L187.81 14.95L202.06 8.84L203.94 7.16L216.95 0.28L216.02 2.31L215.91
          4.94L211.77 13.19L205.44 19.56L202.19 21.77L193.94 25.91L184.81 28.95L177.31 30.02L175.69
          30.98L171.31 31.02L166.28 32.81L167.44 34.00L191.56 34.00L193.31 33.02L198.69 32.98L200.31
          32.02L204.69 31.98L206.91 31.16L206.91 32.94L203.56 38.44L198.19 43.77L191.94 46.91L186.19
          48.05L184.56 49.00L167.44 49.00L165.56 48.00L161.31 48.02L160.34 48.69L160.81 49.77L164.06
          51.16L165.94 52.84L175.19 56.95L181.31 58.98L183.81 59.05L185.31 59.98L191.72 60.05L188.31
          63.67L178.94 68.91L175.56 70.00L163.31 69.98L148.94 64.16L148.34 65.31L151.67 68.69L152.56
          70.56L154.31 71.33L158.81 75.77L165.75 80.00L160.69 81.98L155.44 82.00L153.81 82.95L151.44
          83.00L149.69 82.02L145.31 81.98L142.19 80.95L135.81 77.28L135.16 79.06L143.66 89.98L138.31
          89.98L135.06 88.91L129.56 85.56L126.31 80.33L125.31 80.02L124.33 80.69L123.02 87.69L123.95
          89.19L124.02 92.69L125.05 95.81L127.16 100.06L135.56 108.56L139.84 111.09L139.95
          111.81L136.33 115.69L135.56 117.44L132.31 120.67L131.19 120.95L126.33 116.31L123.16
          112.06L119.77 104.81L118.31 103.34L117.56 103.44L117.02 104.69L117.95 106.19L118.02
          108.69L120.05 114.81L125.95 125.81L121.06 129.84L109.56 135.00L104.06 133.91L95.94
          129.84L91.05 125.81L96.95 114.81L98.98 108.69L99.05 106.19L99.98 104.69L99.31 102.34L98.16
          102.94L93.77 112.19L90.56 116.44L85.94 120.91L84.69 120.67L81.44 117.44L80.67 115.69L77.05
          111.81L77.16 111.09L80.19 109.77L83.69 106.33L85.44 105.56L91.84 97.06L93.05 91.19L94.00
          89.56L93.98 86.31L93.02 84.69L92.67 80.69L91.69 80.02L90.69 80.33L87.44 85.56L81.94
          88.91L78.69 89.98L73.34 89.98L81.84 79.06L81.19 77.28L74.81 80.95L71.69 81.98L67.31
          82.02L65.56 83.00L63.31 82.98L61.56 82.00L56.31 81.98L51.25 80.00L58.19 75.77L61.69
          72.33L63.44 71.56L68.66 65.31L68.06 64.16L53.69 69.98L41.44 70.00L31.94 65.84L25.16
          60.91L25.33 60.02L31.69 59.98L33.19 59.05L35.69 58.98L41.94 56.91L51.06 52.84L52.94
          51.16L56.19 49.77L56.66 48.69L55.69 48.02L51.44 48.00L49.56 49.00L32.44 49.00L30.81
          48.05L25.06 46.91L18.81 43.77L13.44 38.44L10.09 32.94L10.09 31.16L12.31 31.98L15.81
          32.05L17.31 32.98L23.69 33.02L25.44 34.00L49.56 34.00L50.72 32.81L45.69 31.02L41.31
          30.98L39.69 30.02L32.19 28.95L23.06 25.91L14.81 21.77L11.56 19.56L5.23 13.19L1.09 4.94L0.33
          0.34Z
        "
      />
    </svg>
  )
}
