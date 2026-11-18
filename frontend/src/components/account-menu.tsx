import { Link } from "react-router-dom";
import { Bot, ChevronDown, Github, KeyRound, LoaderCircle, LogOut, Palette, Plug, Users } from "lucide-react";
import type { Schema } from "../api";
import { Button } from "./ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { UserAvatar } from "./user-avatar";

export function AccountMenu({ user, onLogout, signingOut }: { user: Schema["UserOut"]; onLogout: () => void; signingOut: boolean }) {
  return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" aria-label={`Account menu for @${user.login}`} title={`@${user.login}`} disabled={signingOut}
                className="relative h-9 gap-1.5 has-[>svg]:px-1.5 aria-expanded:bg-accent pointer-coarse:h-11">
                <UserAvatar user={user} size="sm">
                  {signingOut && <LoaderCircle aria-hidden="true" className="absolute inset-1 size-4 motion-safe:animate-spin" />}
                </UserAvatar>
                <ChevronDown aria-hidden="true" className="size-3.5 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="bottom" align="end" sideOffset={8} collisionPadding={16} className="w-60 max-w-[calc(100vw-2rem)] rounded-lg">
              <DropdownMenuLabel className="flex items-center gap-2 px-2 py-2 font-normal">
                <UserAvatar user={user} className="rounded-lg" />
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
