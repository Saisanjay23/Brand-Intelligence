import React from "react";

interface IconProps {
  size?: number;
  color?: string;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * Custom Brand Intelligence Suite Brand Logo Mark
 */
export function BrandLogoIcon({ size = 28, color, className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <defs>
        <linearGradient id="brandLogoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#00F0FF" />
          <stop offset="50%" stopColor="#7C5CFF" />
          <stop offset="100%" stopColor="#4F46E5" />
        </linearGradient>
      </defs>
      <path
        d="M16 3L26 7.5V14.5C26 21.2 21.7 27.2 16 29.5C10.3 27.2 6 21.2 6 14.5V7.5L16 3Z"
        fill="url(#brandLogoGrad)"
        fillOpacity="0.22"
        stroke="url(#brandLogoGrad)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="16" r="6" stroke="#00F0FF" strokeWidth="1.2" strokeDasharray="2 2" strokeOpacity="0.8" />
      <circle cx="16" cy="16" r="2" fill="#00F0FF" />
      <line x1="16" y1="8" x2="16" y2="24" stroke="#7C5CFF" strokeWidth="1.2" strokeLinecap="round" strokeOpacity="0.7" />
      <line x1="8" y1="16" x2="24" y2="16" stroke="#7C5CFF" strokeWidth="1.2" strokeLinecap="round" strokeOpacity="0.7" />
    </svg>
  );
}

/**
 * Custom Discover Sweep Radar Icon
 */
export function DiscoverIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <circle cx="12" cy="12" r="9" stroke={color} strokeWidth="1.75" strokeOpacity="0.8" />
      <circle cx="12" cy="12" r="5" stroke={color} strokeWidth="1.5" strokeOpacity="0.5" />
      <circle cx="12" cy="12" r="1.75" fill={color} />
      <path
        d="M12 12L18.5 5.5"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M12 3A9 9 0 0 1 21 12"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeOpacity="0.9"
      />
    </svg>
  );
}

/**
 * Custom Threat Deep Analysis & Audit Icon
 */
export function AnalyseIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <path
        d="M12 3L20 7V13C20 17.5 16.5 21.5 12 22.5C7.5 21.5 4 17.5 4 13V7L12 3Z"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeOpacity="0.6"
      />
      <path
        d="M9 12L11 14L15 10"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="6" stroke={color} strokeWidth="1.5" strokeDasharray="2 2" />
    </svg>
  );
}

/**
 * Custom Clients Organization Shield Icon
 */
export function ClientsNavIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <path
        d="M3 21H21"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
      />
      <path
        d="M5 21V7L13 3V21"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M13 10L19 12V21"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line x1="8" y1="10" x2="10" y2="10" stroke={color} strokeWidth="1.5" strokeLinecap="round" />
      <line x1="8" y1="14" x2="10" y2="14" stroke={color} strokeWidth="1.5" strokeLinecap="round" />
      <line x1="16" y1="14" x2="17" y2="14" stroke={color} strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Custom Live Results & Telemetry Icon
 */
export function LiveResultsNavIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <circle cx="12" cy="12" r="9" stroke={color} strokeWidth="1.75" strokeOpacity="0.5" />
      <path
        d="M3.5 12C6 8 8 7 12 12C16 17 18 16 20.5 12"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="2.5" fill={color} />
    </svg>
  );
}

/**
 * Custom Admin Command Center Icon
 */
export function AdminNavIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <circle cx="12" cy="12" r="3" stroke={color} strokeWidth="1.75" />
      <path
        d="M19.4 15A1.65 1.65 0 0 0 19.73 16.82L20.2 17.63A2 2 0 0 1 18.5 20.6L17.57 20.07A1.65 1.65 0 0 0 15.35 20.73L14.88 21.55A2 2 0 0 1 11.4 21.55L10.93 20.73A1.65 1.65 0 0 0 8.71 20.07L7.78 20.6A2 2 0 0 1 6.08 17.63L6.55 16.82A1.65 1.65 0 0 0 6.22 15L5.3 14.47A2 2 0 0 1 5.3 10.99L6.22 10.46A1.65 1.65 0 0 0 6.55 8.64L6.08 7.83A2 2 0 0 1 7.78 4.86L8.71 5.39A1.65 1.65 0 0 0 10.93 4.73L11.4 3.91A2 2 0 0 1 14.88 3.91L15.35 4.73A1.65 1.65 0 0 0 17.57 5.39L18.5 4.86A2 2 0 0 1 20.2 7.83L19.73 8.64A1.65 1.65 0 0 0 19.4 10.46L20.32 10.99A2 2 0 0 1 20.32 14.47L19.4 15Z"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Custom Notification Bell / Alert Beacon
 */
export function BellAlertIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <path
        d="M18 8A6 6 0 0 0 6 8C6 15 3 17 3 17H21S18 15 18 8Z"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M13.73 21A2 2 0 0 1 10.27 21"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Custom Cyber Grid Globe (For All Platforms filter)
 */
export function CyberGlobeIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <circle cx="12" cy="12" r="9" stroke={color} strokeWidth="1.75" />
      <ellipse cx="12" cy="12" rx="4" ry="9" stroke={color} strokeWidth="1.5" strokeOpacity="0.75" />
      <line x1="3" y1="12" x2="21" y2="12" stroke={color} strokeWidth="1.5" strokeOpacity="0.75" />
      <line x1="4.5" y1="7" x2="19.5" y2="7" stroke={color} strokeWidth="1.25" strokeOpacity="0.5" />
      <line x1="4.5" y1="17" x2="19.5" y2="17" stroke={color} strokeWidth="1.25" strokeOpacity="0.5" />
    </svg>
  );
}

/**
 * Custom Session Credentials Key Icon
 */
export function SessionsKeyIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <circle cx="7.5" cy="15.5" r="4.5" stroke={color} strokeWidth="1.75" />
      <path
        d="M10.8 12.2L19.5 3.5M16 7L18.5 9.5M18.5 4.5L20.5 6.5"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Custom Mail Alerts Envelope Icon
 */
export function MailAlertIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <rect x="3" y="5" width="18" height="14" rx="3" stroke={color} strokeWidth="1.75" />
      <path
        d="M3 7L12 13L21 7"
        stroke={color}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Custom Network Proxy Nodes Icon
 */
export function ProxyNodeIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <circle cx="5" cy="6" r="3" stroke={color} strokeWidth="1.75" />
      <circle cx="19" cy="6" r="3" stroke={color} strokeWidth="1.75" />
      <circle cx="12" cy="18" r="3" stroke={color} strokeWidth="1.75" />
      <path
        d="M7.5 7.5L10 15.5M16.5 7.5L14 15.5M8 6H16"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="2 2"
      />
    </svg>
  );
}

/**
 * Custom Scheduler Clock Cycle Icon
 */
export function SchedulerClockIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <circle cx="12" cy="12" r="9" stroke={color} strokeWidth="1.75" />
      <polyline points="12 7 12 12 15.5 14" stroke={color} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Custom Live Activity Waveform Icon
 */
export function ActivityWaveIcon({ size = 16, color = "currentColor", className, style }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
    >
      <path
        d="M2 12H6L9 4L15 20L18 12H22"
        stroke={color}
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
