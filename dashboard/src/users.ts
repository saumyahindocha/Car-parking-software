import { useEffect, useState } from 'react';
import { api, type User } from './api';

let cache: Promise<User[]> | null = null;

/** Users list (cached for the session) to turn user ids into names. */
export function useUsers(): Record<number, User> {
  const [map, setMap] = useState<Record<number, User>>({});
  useEffect(() => {
    if (!cache) cache = api.users().catch(() => {
      cache = null;
      return [];
    });
    let live = true;
    void cache.then((us) => live && setMap(Object.fromEntries(us.map((u) => [u.id, u]))));
    return () => {
      live = false;
    };
  }, []);
  return map;
}

export function invalidateUsers() {
  cache = null;
}

export function userName(map: Record<number, User>, id: number | null | undefined): string {
  if (id === null || id === undefined) return '—';
  return map[id]?.name ?? `user #${id}`;
}
