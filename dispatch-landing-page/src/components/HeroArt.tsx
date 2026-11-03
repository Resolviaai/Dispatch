import { useEffect, useRef } from 'react';

/**
 * The hour stone: a single pale seam of light in a dark, hand-formed mass,
 * with one small figure keeping watch. The minute leaves as a bead of light.
 * Drawn for Dispatch; not a stock photograph.
 */
export function HeroArt() {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const query = window.matchMedia('(max-width: 760px)');
    const apply = () => {
      svgRef.current?.setAttribute('viewBox', query.matches ? '900 0 700 1000' : '0 0 1600 1000');
      svgRef.current?.setAttribute('preserveAspectRatio', 'xMidYMid slice');
    };
    apply();
    query.addEventListener('change', apply);
    return () => query.removeEventListener('change', apply);
  }, []);

  return (
    <svg
      ref={svgRef}
      className="hero-art-svg"
      viewBox="0 0 1600 1000"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <radialGradient id="hero-sky" cx="72%" cy="28%" r="88%">
          <stop offset="0" stopColor="#28331d" />
          <stop offset="0.42" stopColor="#171f11" />
          <stop offset="1" stopColor="#0a0e07" />
        </radialGradient>
        <linearGradient id="hero-stone" x1="0" y1="0" x2="1" y2="0.2">
          <stop offset="0" stopColor="#12170d" />
          <stop offset="0.3" stopColor="#232c19" />
          <stop offset="0.55" stopColor="#39452a" />
          <stop offset="0.78" stopColor="#27311c" />
          <stop offset="1" stopColor="#0e120a" />
        </linearGradient>
        <linearGradient id="hero-stone-rim" x1="0" y1="0" x2="1" y2="0.4">
          <stop offset="0" stopColor="#b8c992" stopOpacity="0.42" />
          <stop offset="0.45" stopColor="#b8c992" stopOpacity="0.04" />
          <stop offset="1" stopColor="#b8c992" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="hero-seam" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#eef5d6" stopOpacity="0" />
          <stop offset="0.18" stopColor="#e7f1c8" stopOpacity="0.9" />
          <stop offset="0.55" stopColor="#f1f7db" />
          <stop offset="0.82" stopColor="#d9e7b4" stopOpacity="0.85" />
          <stop offset="1" stopColor="#eef5d6" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="hero-floor" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1a2312" />
          <stop offset="1" stopColor="#080b05" />
        </linearGradient>
        <radialGradient id="hero-floor-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#cddf9e" stopOpacity="0.38" />
          <stop offset="0.4" stopColor="#8ea460" stopOpacity="0.12" />
          <stop offset="1" stopColor="#8ea460" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="hero-bead-halo" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#f4f9df" stopOpacity="0.9" />
          <stop offset="0.2" stopColor="#e7f1c8" stopOpacity="0.45" />
          <stop offset="0.55" stopColor="#b9cd8a" stopOpacity="0.1" />
          <stop offset="1" stopColor="#b9cd8a" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="hero-haze" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#a7bd7c" stopOpacity="0.22" />
          <stop offset="0.55" stopColor="#6f8253" stopOpacity="0.06" />
          <stop offset="1" stopColor="#6f8253" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="hero-fog" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#141b0e" stopOpacity="0" />
          <stop offset="0.45" stopColor="#141b0e" stopOpacity="0.55" />
          <stop offset="0.75" stopColor="#10150c" stopOpacity="0.9" />
          <stop offset="1" stopColor="#10150c" stopOpacity="0" />
        </linearGradient>
        <filter id="hero-blur-40" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="40" />
        </filter>
        <filter id="hero-blur-18" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="18" />
        </filter>
        <filter id="hero-blur-6" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
        <filter id="hero-blur-3" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
        <filter id="hero-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.82" numOctaves="4" stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
          <feComponentTransfer>
            <feFuncA type="table" tableValues="0 0.16" />
          </feComponentTransfer>
        </filter>
        <filter id="hero-stone-grain">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.008 0.02"
            numOctaves="5"
            seed="12"
            stitchTiles="stitch"
          />
          <feColorMatrix type="saturate" values="0" />
          <feComponentTransfer>
            <feFuncR type="table" tableValues="0.35 0.65" />
            <feFuncG type="table" tableValues="0.35 0.65" />
            <feFuncB type="table" tableValues="0.35 0.65" />
            <feFuncA type="table" tableValues="0 1" />
          </feComponentTransfer>
        </filter>
        <filter id="hero-rough-edge" x="-10%" y="-10%" width="120%" height="120%">
          <feTurbulence type="fractalNoise" baseFrequency="0.01" numOctaves="4" seed="9" result="n" />
          <feDisplacementMap
            in="SourceGraphic"
            in2="n"
            scale="18"
            xChannelSelector="R"
            yChannelSelector="G"
          />
        </filter>
        <clipPath id="hero-stone-clip">
          <path d="M1002 1000 C978 870 972 720 988 575 C1004 420 1030 255 1081 151 C1114 84 1178 55 1238 79 C1307 106 1346 184 1366 286 C1394 438 1400 626 1422 786 C1434 882 1452 954 1473 1000 Z" />
        </clipPath>
      </defs>

      <rect width="1600" height="1000" fill="url(#hero-sky)" />

      <ellipse cx="1210" cy="480" rx="420" ry="420" fill="url(#hero-haze)" filter="url(#hero-blur-40)" />
      <ellipse
        cx="1035"
        cy="520"
        rx="250"
        ry="430"
        fill="url(#hero-haze)"
        opacity="0.85"
        filter="url(#hero-blur-40)"
      />
      <ellipse
        cx="1180"
        cy="840"
        rx="540"
        ry="200"
        fill="url(#hero-haze)"
        opacity="0.7"
        filter="url(#hero-blur-40)"
      />

      <rect y="790" width="1600" height="210" fill="url(#hero-floor)" />
      <ellipse
        cx="1210"
        cy="915"
        rx="430"
        ry="115"
        fill="url(#hero-floor-glow)"
        opacity="0.55"
        filter="url(#hero-blur-18)"
      />

      <g filter="url(#hero-rough-edge)">
        <path
          d="M1002 1000 C978 870 972 720 988 575 C1004 420 1030 255 1081 151 C1114 84 1178 55 1238 79 C1307 106 1346 184 1366 286 C1394 438 1400 626 1422 786 C1434 882 1452 954 1473 1000 Z"
          fill="url(#hero-stone)"
        />
      </g>
      <g clipPath="url(#hero-stone-clip)" opacity="0.68" style={{ mixBlendMode: 'overlay' }}>
        <rect x="950" y="40" width="560" height="980" filter="url(#hero-stone-grain)" />
      </g>
      <g clipPath="url(#hero-stone-clip)" opacity="0.3" style={{ mixBlendMode: 'soft-light' }}>
        <rect x="950" y="40" width="560" height="980" filter="url(#hero-stone-grain)" />
      </g>
      <path
        d="M1002 1000 C978 870 972 720 988 575 C1004 420 1030 255 1081 151 C1114 84 1178 55 1238 79"
        fill="none"
        stroke="url(#hero-stone-rim)"
        strokeWidth="7"
      />
      <path
        d="M1002 1000 C978 870 972 720 988 575 C1004 420 1030 255 1081 151 C1114 84 1178 55 1238 79"
        fill="none"
        stroke="#cddb9f"
        strokeWidth="1.4"
        opacity="0.32"
        filter="url(#hero-blur-3)"
      />
      <g stroke="#0b0f08" strokeWidth="1.2" fill="none" opacity="0.55">
        <path d="M1062 220 C1078 300 1068 380 1090 450 C1106 502 1102 560 1114 620" />
        <path d="M1300 300 C1284 380 1296 450 1276 520" />
        <path d="M1040 690 C1060 740 1050 800 1070 860" />
      </g>

      <path
        d="M1432 1000 C1422 920 1420 830 1428 750 C1438 660 1454 590 1478 548 C1500 510 1526 520 1536 562 C1550 620 1548 720 1554 810 C1559 890 1566 950 1574 1000 Z"
        fill="#1c2515"
        opacity="0.85"
      />

      <ellipse cx="1180" cy="985" rx="250" ry="26" fill="#050704" opacity="0.65" filter="url(#hero-blur-6)" />

      <g className="hero-seam" clipPath="url(#hero-stone-clip)">
        <path
          d="M1188 210 C1180 300 1177 390 1182 470 C1175 540 1174 620 1181 700 C1185 750 1183 780 1188 815"
          fill="none"
          stroke="#d9e7b4"
          strokeWidth="10"
          opacity="0.18"
          filter="url(#hero-blur-6)"
        />
        <path
          d="M1187 200 C1179 300 1176 390 1181 470 C1174 540 1173 620 1180 700 C1184 750 1182 780 1187 815 C1195 750 1198 700 1193 630 C1200 550 1202 470 1196 390 C1201 320 1198 250 1195 200 Z"
          fill="url(#hero-seam)"
          filter="url(#hero-blur-3)"
          opacity="0.8"
        />
        <path
          d="M1189 235 C1183 330 1181 430 1185 510 C1180 590 1179 680 1184 770"
          fill="none"
          stroke="#f6fadf"
          strokeWidth="1.2"
          opacity="0.65"
          filter="url(#hero-blur-3)"
        />
        <path
          d="M1183 540 C1170 552 1162 572 1160 594 M1186 640 C1198 652 1204 670 1204 690"
          fill="none"
          stroke="#c9db98"
          strokeWidth="1"
          opacity="0.28"
          filter="url(#hero-blur-3)"
        />
      </g>

      <path
        d="M1190 565 C1204 570 1218 590 1224 616"
        fill="none"
        stroke="#e8f2c8"
        strokeWidth="1.3"
        opacity="0.75"
      />
      <g className="hero-bead">
        <circle cx="1228" cy="622" r="54" fill="url(#hero-bead-halo)" />
        <circle cx="1228" cy="622" r="7" fill="#f6fae7" />
        <circle cx="1228" cy="622" r="2.6" fill="#ffffff" />
      </g>

      <path d="M1180 792 L1020 1000 L1370 1000 Z" fill="#c9db98" opacity="0.07" filter="url(#hero-blur-18)" />
      <path d="M1185 792 L1110 1000 L1250 1000 Z" fill="#e3eec2" opacity="0.09" filter="url(#hero-blur-6)" />
      <path
        d="M1187 815 C1174 850 1158 910 1138 1000 L1236 1000 C1216 910 1202 850 1194 815 Z"
        fill="#d9e7b4"
        opacity="0.12"
        filter="url(#hero-blur-18)"
      />

      <g className="hero-figure" transform="translate(1049 855) scale(1.12) translate(-1049 -855)">
        <g fill="#070a04">
          <circle cx="1049" cy="794" r="10.5" />
          <path d="M1033 812 Q1049 803 1065 812 L1073 868 L1064 870 L1060 908 L1053 908 L1050 874 L1047 908 L1040 908 L1036 870 L1027 868 Z" />
        </g>
        <path
          d="M1058 789 C1064 792 1067 799 1066 807 M1068 820 C1074 838 1073 858 1066 872"
          stroke="#c3d49c"
          strokeWidth="1.8"
          fill="none"
          opacity="0.7"
        />
      </g>
      <ellipse
        cx="1050"
        cy="918"
        rx="26"
        ry="5"
        fill="#050704"
        opacity="0.55"
        filter="url(#hero-blur-6)"
      />

      <rect y="720" width="1600" height="280" fill="url(#hero-fog)" opacity="0.85" />
      <ellipse cx="1180" cy="870" rx="500" ry="100" fill="#1a2312" opacity="0.45" filter="url(#hero-blur-40)" />

      <g className="hero-dust" fill="#dce8b5">
        <circle cx="1120" cy="260" r="1.6" opacity="0.4" />
        <circle cx="1270" cy="180" r="1.2" opacity="0.35" />
        <circle cx="1340" cy="420" r="1.8" opacity="0.3" />
        <circle cx="1080" cy="480" r="1.3" opacity="0.4" />
        <circle cx="1240" cy="700" r="1.5" opacity="0.3" />
        <circle cx="980" cy="620" r="1.1" opacity="0.3" />
        <circle cx="1420" cy="620" r="1.4" opacity="0.28" />
        <circle cx="1160" cy="380" r="1.1" opacity="0.35" />
        <circle cx="1300" cy="820" r="1.6" opacity="0.25" />
        <circle cx="1050" cy="220" r="1.2" opacity="0.25" />
      </g>

      <rect
        width="1600"
        height="1000"
        filter="url(#hero-grain)"
        opacity="0.55"
        style={{ mixBlendMode: 'soft-light' }}
      />
    </svg>
  );
}
