'use client';

import { useState, useEffect } from 'react';
import { X, Plus, Minus } from 'lucide-react';
import { useApi } from '@/hooks/useApi';

interface Role {
  id: number;
  name: string;
  description: string;
  permissions: string[];
}

interface RoleAssignmentModalProps {
  user: any;
  onClose: () => void;
  onSuccess: () => void;
}

export function RoleAssignmentModal({ user, onClose, onSuccess }: RoleAssignmentModalProps) {
  const [availableRoles, setAvailableRoles] = useState<Role[]>([]);
  const [userRoles, setUserRoles] = useState<Role[]>(user.roles || []);
  const [isLoading, setIsLoading] = useState(false);
  const api = useApi();

  useEffect(() => {
    fetchRoles();
  }, []);

  const fetchRoles = async () => {
    try {
      const response = await api.get(`/api/roles?company_id=${user.company_id}`);
      setAvailableRoles(response.data.items);
    } catch (error) {
      console.error('Failed to fetch roles:', error);
    }
  };

  const isRoleAssigned = (roleId: number) => {
    return userRoles.some(role => role.id === roleId);
  };

  const handleAssignRole = async (role: Role) => {
    try {
      setIsLoading(true);
      await api.post(`/api/roles/${role.id}/assign-to-user/${user.id}`);
      setUserRoles([...userRoles, role]);
      onSuccess();
    } catch (error) {
      console.error('Failed to assign role:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleRemoveRole = async (role: Role) => {
    try {
      setIsLoading(true);
      await api.delete(`/api/roles/${role.id}/remove-from-user/${user.id}`);
      setUserRoles(userRoles.filter(r => r.id !== role.id));
      onSuccess();
    } catch (error) {
      console.error('Failed to remove role:', error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <div className="flex justify-between items-center mb-4">
          <div>
            <h2 className="text-xl font-semibold">Manage User Roles</h2>
            <p className="text-sm text-gray-600">
              User: {user.first_name} {user.last_name} ({user.username})
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1">
          <div className="space-y-2">
            {availableRoles.map((role) => {
              const isAssigned = isRoleAssigned(role.id);
              
              return (
                <div
                  key={role.id}
                  className={`p-4 border rounded-lg ${
                    isAssigned ? 'bg-blue-50 border-blue-300' : 'bg-white'
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="font-medium text-gray-900">{role.name}</h3>
                      {role.description && (
                        <p className="text-sm text-gray-600 mt-1">{role.description}</p>
                      )}
                      {role.permissions.length > 0 && (
                        <div className="mt-2">
                          <p className="text-xs text-gray-500 mb-1">Permissions:</p>
                          <div className="flex flex-wrap gap-1">
                            {role.permissions.map((permission) => (
                              <span
                                key={permission}
                                className="px-2 py-1 text-xs bg-gray-100 text-gray-700 rounded"
                              >
                                {permission}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() =>
                        isAssigned ? handleRemoveRole(role) : handleAssignRole(role)
                      }
                      disabled={isLoading}
                      className={`ml-4 p-2 rounded-lg transition-colors ${
                        isAssigned
                          ? 'bg-red-100 text-red-600 hover:bg-red-200'
                          : 'bg-green-100 text-green-600 hover:bg-green-200'
                      } disabled:opacity-50`}
                    >
                      {isAssigned ? (
                        <Minus className="h-4 w-4" />
                      ) : (
                        <Plus className="h-4 w-4" />
                      )}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="mt-4 pt-4 border-t">
          <div className="flex justify-between items-center">
            <p className="text-sm text-gray-600">
              {userRoles.length} role{userRoles.length !== 1 ? 's' : ''} assigned
            </p>
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
} 