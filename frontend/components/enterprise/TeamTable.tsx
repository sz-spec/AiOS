"use client";
import { useEffect, useState } from "react";

interface Member {
  id: string;
  email: string;
  name: string;
  role: string;
  joinedAt: string;
}

const ROLE_COLORS: Record<string, string> = {
  owner: "bg-purple-100 text-purple-700",
  admin: "bg-red-100 text-red-700",
  editor: "bg-blue-100 text-blue-700",
  viewer: "bg-gray-100 text-gray-700",
};

export default function TeamTable() {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/teams/members")
      .then((res) => res.json())
      .then((data) => setMembers(data.members || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-4 text-gray-500">Loading team...</div>;

  return (
    <div className="border rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-b">
          <tr>
            <th className="text-left px-4 py-3 font-medium">Member</th>
            <th className="text-left px-4 py-3 font-medium">Role</th>
            <th className="text-left px-4 py-3 font-medium">Joined</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {members.map((m) => (
            <tr key={m.id} className="hover:bg-gray-50">
              <td className="px-4 py-3">
                <div className="font-medium">{m.name || m.email}</div>
                <div className="text-xs text-gray-500">{m.email}</div>
              </td>
              <td className="px-4 py-3">
                <span className={`text-xs px-2 py-0.5 rounded-full ${ROLE_COLORS[m.role] || ROLE_COLORS.viewer}`}>
                  {m.role}
                </span>
              </td>
              <td className="px-4 py-3 text-gray-500">
                {new Date(m.joinedAt).toLocaleDateString()}
              </td>
            </tr>
          ))}
          {members.length === 0 && (
            <tr>
              <td colSpan={3} className="px-4 py-8 text-center text-gray-400">
                No team members yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
