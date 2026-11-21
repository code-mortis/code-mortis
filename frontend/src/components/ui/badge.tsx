import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "cn";
import { Slot } from "radix-ui";

const badgeRadius = "rounded-[5px]";

const badgeVariants = cva(
  `inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden ${badgeRadius} border border-transparent px-2 py-[3px] text-xs leading-[17px] font-semibold whitespace-nowrap transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3`,
  {
    variants: {
      variant: {
        default:
          "bg-[color:color-mix(in_srgb,var(--card)_90%,var(--primary))] text-[color:color-mix(in_srgb,var(--primary)_85%,var(--foreground))] underline-offset-4 [a&]:hover:underline",
        secondary:
          "bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        destructive:
          "bg-[color:color-mix(in_srgb,var(--card)_90%,var(--destructive))] text-[color:color-mix(in_srgb,var(--destructive)_85%,var(--foreground))] underline-offset-4 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 [a&]:hover:underline",
        outline:
          "border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        ghost: "[a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        link: "text-primary underline-offset-4 [a&]:hover:underline",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span";

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  );
}

export { Badge, badgeRadius, badgeVariants };
