import { toast } from "react-hot-toast";

export const confirmAction = (message: string): Promise<boolean> => {
  return new Promise((resolve) => {
    toast(
      (t) => (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <span style={{ fontWeight: 500, fontSize: "14px", lineHeight: "1.5" }}>{message}</span>
          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
            <button 
              onClick={() => {
                toast.dismiss(t.id);
                resolve(false);
              }} 
              style={{ padding: "8px 14px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "6px", color: "var(--text-main)", cursor: "pointer", fontSize: "13px", transition: "all 0.2s" }}
            >
              Cancel
            </button>
            <button 
              onClick={() => {
                toast.dismiss(t.id);
                resolve(true);
              }} 
              style={{ padding: "8px 14px", background: "linear-gradient(135deg, var(--primary-color, #8838DD) 0%, #5a1ea0 100%)", border: "1px solid rgba(136, 56, 221, 0.5)", borderRadius: "6px", color: "#fff", cursor: "pointer", fontWeight: 600, fontSize: "13px", transition: "all 0.2s" }}
            >
              Confirm
            </button>
          </div>
        </div>
      ),
      { 
        duration: Infinity, 
        style: { 
          background: "var(--background-color-dark2, #1D2939)", 
          color: "#fff", 
          border: "1px solid rgba(255,255,255,0.1)", 
          minWidth: "340px", 
          boxShadow: "0 8px 32px rgba(0,0,0,0.5)", 
          backdropFilter: "blur(12px)" 
        } 
      }
    );
  });
};
