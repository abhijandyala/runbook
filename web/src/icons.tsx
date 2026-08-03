type IconProps = { className?: string };

export function RunbookMark({ className = "w-6 h-6" }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#171719" stroke="#2A2A30" />
      <path d="M8 7h9a5.5 5.5 0 0 1 0 11h-3v7H8V7Zm6 5v2h2.5a1 1 0 0 0 0-2H14Z" fill="#FFCE1B" />
      <path d="m17 17 6 8h-6l-4-8h4Z" fill="#069494" />
    </svg>
  );
}

export function SlackBrand({ className = "w-5 h-5" }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#E01E5A" d="M5.04 15.17a2.52 2.52 0 1 1-2.52-2.52h2.52v2.52Zm1.27 0a2.52 2.52 0 1 1 5.04 0v6.31a2.52 2.52 0 1 1-5.04 0v-6.31Z" />
      <path fill="#36C5F0" d="M8.83 5.04a2.52 2.52 0 1 1 2.52-2.52v2.52H8.83Zm0 1.27a2.52 2.52 0 1 1 0 5.04H2.52a2.52 2.52 0 1 1 0-5.04h6.31Z" />
      <path fill="#2EB67D" d="M18.96 8.83a2.52 2.52 0 1 1 2.52 2.52h-2.52V8.83Zm-1.27 0a2.52 2.52 0 1 1-5.04 0V2.52a2.52 2.52 0 1 1 5.04 0v6.31Z" />
      <path fill="#ECB22E" d="M15.17 18.96a2.52 2.52 0 1 1-2.52 2.52v-2.52h2.52Zm0-1.27a2.52 2.52 0 1 1 0-5.04h6.31a2.52 2.52 0 1 1 0 5.04h-6.31Z" />
    </svg>
  );
}

export function LinearBrand({ className = "w-5 h-5" }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 100 100" fill="#5E6AD2" aria-hidden="true">
      <path d="M2 63 37 98c-1-.4-2-.8-3-1L2 63Zm-2-8 57 44 5-1L1 39c-.5 2-1 4-1 6v10ZM1 47l58 52c2-1 4-2 6-4L4 36c-2 3-3 7-3 11Zm6-17 60 54 7-6-68-54-5 6Zm10-10 67 60 4-7-65-58-6 5Zm11-8 61 54 3-6L35 8l-7 4Zm13-6 50 45c1-4 1-8 0-12L53 4c-4 0-8 1-12 2Zm22-2 27 23c-1-6-4-11-9-15S70 3 63 4Z" />
    </svg>
  );
}

export function GitHubBrand({ className = "w-5 h-5" }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="#E8EDF4" aria-hidden="true">
      <path d="M8 .2a8 8 0 0 0-2.5 15.6c.4.1.5-.2.5-.4V14c-2.2.5-2.7-1-2.7-1a2 2 0 0 0-.9-1.2c-.7-.5.1-.5.1-.5a1.7 1.7 0 0 1 1.2.8 1.7 1.7 0 0 0 2.3.7c.1-.4.3-.8.5-1.1-1.8-.2-3.6-.9-3.6-4a3 3 0 0 1 .8-2.2 2.9 2.9 0 0 1 .1-2.1s.7-.2 2.2.8a7.6 7.6 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8a2.9 2.9 0 0 1 .1 2.1 3 3 0 0 1 .8 2.2c0 3.1-1.9 3.8-3.6 4a1.9 1.9 0 0 1 .5 1.5v2.2c0 .2.1.5.5.4A8 8 0 0 0 8 .2Z" />
    </svg>
  );
}

export function NavIcon({ name, className = "w-4 h-4" }: IconProps & { name: string }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const
  };
  if (name === "dashboard")
    return <svg className={className} viewBox="0 0 16 16" {...common}><rect x="1" y="1" width="6" height="6" rx="1" /><rect x="9" y="1" width="6" height="4" rx="1" /><rect x="1" y="9" width="6" height="4" rx="1" /><rect x="9" y="7" width="6" height="6" rx="1" /></svg>;
  if (name === "inbox")
    return <svg className={className} viewBox="0 0 16 16" {...common}><rect x="1" y="3" width="14" height="10" rx="1.5" /><path d="M1 10h4l1 2h4l1-2h4" /></svg>;
  if (name === "memory")
    return <svg className={className} viewBox="0 0 16 16" {...common}><circle cx="8" cy="8" r="6.5" /><circle cx="8" cy="8" r="2" /><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" /></svg>;
  if (name === "settings")
    return <svg className={className} viewBox="0 0 16 16" {...common}><circle cx="8" cy="8" r="2.5" /><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5 13 13M3 13l1.5-1.5M11.5 4.5 13 3" /></svg>;
  return <svg className={className} viewBox="0 0 16 16" {...common}><path d="M8 2a4 4 0 0 0-4 4v2.5L2.5 11v1h11v-1L12 8.5V6a4 4 0 0 0-4-4Z" /><path d="M6.5 14a1.5 1.5 0 0 0 3 0" /></svg>;
}
