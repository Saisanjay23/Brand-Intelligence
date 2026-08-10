interface Props {
  platform: string;
  size?: number;
  color?: string;
}

export function PlatformIcon({ platform, size = 20, color }: Props) {
  const p = (platform || "").toLowerCase();

  switch (p) {
    case "facebook":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="12" fill={color || "#1877F2"} />
          <path
            d="M16.67 15.47l.53-3.47h-3.33V9.87c0-.95.46-1.87 1.95-1.87h1.51V5.15s-1.37-.23-2.68-.23c-2.73 0-4.52 1.66-4.52 4.66V12H7v3.47h3.13V24h3.87v-8.53h2.67z"
            fill="#fff"
          />
        </svg>
      );
    case "instagram":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
          <defs>
            <linearGradient
              id="ig-grad-icon"
              x1="0%"
              y1="100%"
              x2="100%"
              y2="0%"
            >
              <stop offset="0%" stopColor="#FFDC80" />
              <stop offset="25%" stopColor="#F77737" />
              <stop offset="50%" stopColor="#E1306C" />
              <stop offset="75%" stopColor="#C13584" />
              <stop offset="100%" stopColor="#833AB4" />
            </linearGradient>
          </defs>
          <rect
            width="24"
            height="24"
            rx="6"
            fill={color || "url(#ig-grad-icon)"}
          />
          <rect
            x="4"
            y="4"
            width="16"
            height="16"
            rx="4"
            stroke="#fff"
            strokeWidth="1.8"
            fill="none"
          />
          <circle
            cx="12"
            cy="12"
            r="3.8"
            stroke="#fff"
            strokeWidth="1.8"
            fill="none"
          />
          <circle cx="17.2" cy="6.8" r="1.2" fill="#fff" />
        </svg>
      );
    case "twitter":
    case "x":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="12" fill={color || "#000"} />
          <path
            d="M13.52 10.77L17.95 5.5h-1.05l-3.84 4.57L10.2 5.5H6l4.64 6.93L6 18.5h1.05l4.06-4.83L14.1 18.5h4.2l-4.78-7.73z"
            fill="#fff"
          />
        </svg>
      );
    case "youtube":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
          <rect width="24" height="24" rx="6" fill={color || "#FF0000"} />
          <path d="M10 15l4.5-3L10 9v6z" fill="#fff" />
        </svg>
      );
    case "telegram":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="12" fill={color || "#26A5E4"} />
          <path
            d="M5.43 11.87l11.53-4.45c.53-.19 1 .13.82.95l-1.96 9.24c-.14.66-.53.82-1.08.51l-3-2.21-1.45 1.4c-.16.16-.3.3-.61.3l.22-3.06 5.57-5.03c.24-.22-.05-.34-.38-.13l-6.88 4.34-2.96-.93c-.64-.2-.66-.64.14-.95z"
            fill="#fff"
          />
        </svg>
      );
    default:
      return <span>🌐</span>;
  }
}
