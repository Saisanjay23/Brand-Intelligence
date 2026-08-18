import { useEffect, useRef, useState } from "react";
import { SearchIcon } from "./AppIcons";

export interface ClientOption {
  client_id: string;
  name?: string;
}

interface Props {
  clients: ClientOption[];
  selectedClientId: string;
  onSelectClient: (clientId: string, name?: string) => void;
  loading?: boolean;
  placeholder?: string;
  disabled?: boolean;
  style?: React.CSSProperties;
}

export function CustomClientSelect({
  clients,
  selectedClientId,
  onSelectClient,
  loading = false,
  placeholder = "— choose a client —",
  disabled = false,
  style,
}: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  const selectedClient = clients.find((c) => c.client_id === selectedClientId);
  const label = selectedClient ? (selectedClient.name || selectedClient.client_id) : "";
  const initial = label ? label.charAt(0).toUpperCase() : "";

  // Close dropdown on click outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const filteredClients = clients.filter((c) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      (c.name && c.name.toLowerCase().includes(q)) ||
      c.client_id.toLowerCase().includes(q)
    );
  });

  return (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        width: "100%",
        userSelect: "none",
        ...style,
      }}
    >
      {/* Trigger Box / Chip */}
      <div
        onClick={() => {
          if (!disabled && !loading) setIsOpen((prev) => !prev);
        }}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "10px",
          padding: "10px 14px",
          background: "var(--bg-surface-2, #101828)",
          border: `1px solid ${isOpen ? "var(--cyan, #8838DD)" : "var(--border-subtle, #344054)"}`,
          borderRadius: "12px",
          cursor: disabled || loading ? "not-allowed" : "pointer",
          transition: "all 0.2s ease",
          boxShadow: isOpen ? "0 0 12px rgba(136, 56, 221, 0.25)" : "none",
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0, flex: 1 }}>
          {selectedClient ? (
            <span
              style={{
                width: "26px",
                height: "26px",
                borderRadius: "50%",
                background: "linear-gradient(135deg, var(--cyan, #8838DD), var(--purple, #7727CD))",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "12px",
                fontWeight: 800,
                color: "#fff",
                flexShrink: 0,
              }}
            >
              {initial}
            </span>
          ) : (
            <span style={{ fontSize: "14px", opacity: 0.6 }}>📂</span>
          )}
          <div style={{ minWidth: 0, flex: 1 }}>
            {selectedClient ? (
              <span
                style={{
                  fontSize: "13px",
                  fontWeight: 600,
                  color: "var(--text-main, #fff)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  display: "block",
                }}
              >
                {selectedClient.name || selectedClient.client_id}
                {selectedClient.name && (
                  <span style={{ fontSize: "12px", color: "var(--text-dim, #98A2B3)", marginLeft: "6px", fontWeight: 400 }}>
                    ({selectedClient.client_id})
                  </span>
                )}
              </span>
            ) : (
              <span style={{ fontSize: "13px", color: "var(--text-dim, #98A2B3)" }}>
                {loading ? "Loading clients…" : clients.length ? placeholder : "No saved clients yet"}
              </span>
            )}
          </div>
        </div>
        <span
          style={{
            fontSize: "10px",
            color: "var(--text-dim, #98A2B3)",
            transform: isOpen ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 0.2s ease",
          }}
        >
          ▼
        </span>
      </div>

      {/* Floating Custom Dropdown Popup */}
      {isOpen && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            right: 0,
            zIndex: 100,
            background: "var(--bg-elevated-grad, #101828)",
            border: "1px solid var(--border-subtle, #344054)",
            borderRadius: "14px",
            boxShadow: "0 16px 48px rgba(0, 0, 0, 0.7), 0 0 20px rgba(136, 56, 221, 0.15)",
            backdropFilter: "blur(16px)",
            padding: "10px",
            animation: "fadeUp 0.15s ease-out",
          }}
        >
          {/* Search Input */}
          {clients.length > 5 && (
            <div style={{ marginBottom: "8px", position: "relative", display: "flex", alignItems: "center" }}>
              <SearchIcon size={13} color="var(--text-muted, #98a2b3)" style={{ position: "absolute", left: "10px", pointerEvents: "none" }} />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search saved clients…"
                style={{
                  width: "100%",
                  padding: "8px 12px 8px 30px",
                  fontSize: "12px",
                  background: "var(--bg-surface-3, #1D2939)",
                  border: "1px solid var(--border-color, #344054)",
                  borderRadius: "8px",
                  color: "var(--text-main, #fff)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
                autoFocus
              />
            </div>
          )}

          {/* Client List */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "4px",
              maxHeight: "240px",
              overflowY: "auto",
            }}
          >
            {filteredClients.length === 0 ? (
              <div style={{ padding: "12px", fontSize: "12px", color: "var(--text-muted, #98A2B3)", textAlign: "center" }}>
                No clients match "{searchQuery}"
              </div>
            ) : (
              filteredClients.map((c) => {
                const isSelected = c.client_id === selectedClientId;
                const cLabel = c.name || c.client_id;
                const cInitial = cLabel.charAt(0).toUpperCase();

                return (
                  <button
                    key={c.client_id}
                    type="button"
                    onClick={() => {
                      onSelectClient(c.client_id, c.name);
                      setIsOpen(false);
                      setSearchQuery("");
                    }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "10px",
                      width: "100%",
                      padding: "8px 12px",
                      borderRadius: "8px",
                      background: isSelected ? "rgba(136, 56, 221, 0.18)" : "transparent",
                      border: isSelected ? "1px solid var(--cyan, #8838DD)" : "1px solid transparent",
                      color: "var(--text-main, #fff)",
                      fontSize: "13px",
                      cursor: "pointer",
                      textAlign: "left",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => {
                      if (!isSelected) e.currentTarget.style.background = "var(--bg-hover, #1D2939)";
                    }}
                    onMouseLeave={(e) => {
                      if (!isSelected) e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <span
                      style={{
                        width: "24px",
                        height: "24px",
                        borderRadius: "50%",
                        background: isSelected
                          ? "linear-gradient(135deg, var(--cyan, #8838DD), var(--purple, #7727CD))"
                          : "var(--bg-hover, #344054)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: "11px",
                        fontWeight: 700,
                        flexShrink: 0,
                      }}
                    >
                      {cInitial}
                    </span>
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      <strong>{c.name || c.client_id}</strong>
                      {c.name && (
                        <span style={{ color: "var(--text-dim, #98A2B3)", fontSize: "11px", marginLeft: "6px" }}>
                          ({c.client_id})
                        </span>
                      )}
                    </span>
                    {isSelected && (
                      <span style={{ color: "var(--cyan, #8838DD)", fontWeight: 700, fontSize: "14px" }}>✓</span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
