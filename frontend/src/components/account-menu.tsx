import { Link } from "react-router-dom";
import { Bot, Github, KeyRound, LoaderCircle, LogOut, Palette, Plug, Users } from "lucide-react";
import type { Schema } from "../api";
import { Button } from "./ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";

export function AccountMenu({ user, onLogout, signingOut }: { user: Schema["UserOut"]; onLogout: () => void; signingOut: boolean }) {
  return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-9 shrink-0" aria-label={`Account menu for @${user.login}`} title={`@${user.login}`} disabled={signingOut}>
                <Avatar className="rounded-lg">
                  <AvatarImage src={user.avatar_url || undefined} alt="" className="object-cover" />
                  <AvatarFallback className="rounded-lg bg-sidebar-accent text-xs font-medium text-sidebar-accent-foreground">{user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
                </Avatar>
                {signingOut && <LoaderCircle aria-hidden="true" className="absolute size-4 motion-safe:animate-spin" />}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="bottom" align="end" sideOffset={8} collisionPadding={16} className="w-60 max-w-[calc(100vw-2rem)] rounded-lg">
              <DropdownMenuLabel className="flex items-center gap-2 px-2 py-2 font-normal">
                <Avatar className="shrink-0 rounded-lg">
                  <AvatarImage src={user.avatar_url || undefined} alt="" className="object-cover" />
                  <AvatarFallback className="rounded-lg bg-sidebar-accent text-xs font-medium text-sidebar-accent-foreground">{user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
                </Avatar>
                <span className="min-w-0">
                  <span className="block truncate font-medium" title={user.login}>{user.login}</span>
                  <span className="block text-xs text-muted-foreground">{user.role === "admin" ? "Administrator" : "Member"}</span>
                </span>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=connections"><Plug aria-hidden="true" />AI connections</Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=api-tokens"><KeyRound aria-hidden="true" />API tokens</Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=mcp"><Bot aria-hidden="true" />MCP access</Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=appearance"><Palette aria-hidden="true" />Appearance</Link>
                </DropdownMenuItem>
                {user.role === "admin" && <>
                  <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                    <Link to="/settings?tab=github"><Github aria-hidden="true" />GitHub</Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                    <Link to="/settings?tab=team"><Users aria-hidden="true" />Team</Link>
                  </DropdownMenuItem>
                </>}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onLogout} disabled={signingOut} className="min-h-10 lg:min-h-8"><LogOut aria-hidden="true" />Sign out</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
  );
}
