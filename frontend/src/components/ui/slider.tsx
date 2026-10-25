import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";

export interface SliderProps extends React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root> {}

export const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  SliderProps
>(({ className, ...props }, ref) => (
  <SliderPrimitive.Root
    ref={ref}
    className={`relative flex w-full touch-none select-none items-center cursor-pointer ${className || ''}`}
    {...props}
  >
    <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-white/20">
      <SliderPrimitive.Range className="absolute h-full bg-primary" />
    </SliderPrimitive.Track>
    <SliderPrimitive.Thumb
      aria-label="Zoom Level"
      className="block h-5 w-5 rounded-full border-2 border-primary bg-white shadow-[0_0_12px_rgba(37,99,235,0.7)] transition-transform hover:scale-110 active:scale-125 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
    />
  </SliderPrimitive.Root>
));
Slider.displayName = SliderPrimitive.Root.displayName;
