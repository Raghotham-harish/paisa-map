// Dynamic script-load idiom mirroring useGoogleSignIn's Google Identity
// Services loader in ./auth.tsx — cached promise, checks the global first.

declare global {
  interface Window {
    Razorpay: any;
  }
}

let loadPromise: Promise<void> | null = null;

export function loadRazorpayCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  if (loadPromise) return loadPromise;
  loadPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load Razorpay Checkout"));
    document.head.appendChild(script);
  });
  return loadPromise;
}

export interface RazorpaySuccessResponse {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

export async function openCheckout(opts: {
  key: string;
  amount: number;
  currency: string;
  order_id: string;
  description: string;
  prefillEmail?: string;
  onSuccess: (resp: RazorpaySuccessResponse) => void;
  onDismiss?: () => void;
}) {
  await loadRazorpayCheckout();
  const rzp = new window.Razorpay({
    key: opts.key,
    amount: opts.amount,
    currency: opts.currency,
    order_id: opts.order_id,
    name: "PaisaMap",
    description: opts.description,
    prefill: { email: opts.prefillEmail },
    theme: { color: "#216A0B" },
    handler: (resp: RazorpaySuccessResponse) => opts.onSuccess(resp),
    modal: { ondismiss: opts.onDismiss },
  });
  rzp.open();
}
