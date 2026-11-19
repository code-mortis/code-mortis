import type { ComponentProps, ReactNode } from "react";
import { cn } from "cn";
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";

export function UserAvatar({ user, className, size, children }: {
  user: { login: string; avatar_url?: string };
  className?: string;
  size?: ComponentProps<typeof Avatar>["size"];
  children?: ReactNode;
}) {
  return <Avatar aria-hidden="true" size={size} className={cn("rounded-md", className)}>
    <AvatarImage src={user.avatar_url || undefined} alt="" className="object-cover" />
    <AvatarFallback className="rounded-none bg-sidebar-accent text-xs font-medium text-sidebar-accent-foreground">
      {user.login.slice(0, 2).toUpperCase()}
    </AvatarFallback>
    {children}
  </Avatar>;
}
