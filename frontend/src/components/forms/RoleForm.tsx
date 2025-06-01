'use client';

import { useState, useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { X } from 'lucide-react';
import { useApi } from '@/hooks/useApi';

const roleSchema = z.object({
  name: z.string().min(1, 'Role name is required'),
  description: z.string().optional(),
  permissions: z.array(z.string()).default([]),
});

type RoleFormData = z.infer<typeof roleSchema>;

interface PermissionGroup {
  module: string;
  permissions: string[];
}

interface RoleFormProps {
  role?: any;
  onClose: () => void;
  onSuccess: () => void;
}

const availablePermissions: PermissionGroup[] = [
  { module: 'gl', permissions: ['view', 'create', 'edit', 'delete', 'post'] },
  { module: 'ar', permissions: ['view', 'create', 'edit', 'delete', 'post', 'allocate'] },
  { module: 'ap', permissions: ['view', 'create', 'edit', 'delete', 'post', 'allocate'] },
  { module: 'inventory', permissions: ['view', 'create', 'edit', 'delete', 'adjust'] },
  { module: 'oe', permissions: ['view', 'create', 'edit', 'delete', 'approve'] },
  { module: 'users', permissions: ['view', 'create', 'edit', 'delete', 'assign_roles'] },
  { module: 'companies', permissions: ['view', 'create', 'edit', 'delete'] },
  { module: 'reports', permissions: ['view', 'export'] },
  { module: 'system', permissions: ['configure', 'backup', 'restore'] },
];

export function RoleForm({ role, onClose, onSuccess }: RoleFormProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [selectedPermissions, setSelectedPermissions] = useState<string[]>(
    role?.permissions || []
  );
  const api = useApi();

  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
  } = useForm<RoleFormData>({
    resolver: zodResolver(roleSchema),
    defaultValues: {
      name: role?.name || '',
      description: role?.description || '',
      permissions: role?.permissions || [],
    },
  });

  const togglePermission = (permission: string) => {
    setSelectedPermissions((prev) => {
      if (prev.includes(permission)) {
        return prev.filter((p) => p !== permission);
      } else {
        return [...prev, permission];
      }
    });
  };

  const toggleModule = (module: string, permissions: string[]) => {
    const modulePermissions = permissions.map((p) => `${module}.${p}`);
    const allSelected = modulePermissions.every((p) => selectedPermissions.includes(p));

    if (allSelected) {
      // Remove all module permissions
      setSelectedPermissions((prev) =>
        prev.filter((p) => !modulePermissions.includes(p))
      );
    } else {
      // Add all module permissions
      setSelectedPermissions((prev) => {
        const newPermissions = [...prev];
        modulePermissions.forEach((p) => {
          if (!newPermissions.includes(p)) {
            newPermissions.push(p);
          }
        });
        return newPermissions;
      });
    }
  };

  const onSubmit = async (data: RoleFormData) => {
    try {
      setIsLoading(true);
      
      const payload = {
        ...data,
        permissions: selectedPermissions,
      };
      
      if (role) {
        // Update existing role
        await api.put(`/api/roles/${role.id}`, payload);
      } else {
        // Create new role
        await api.post('/api/roles', payload);
      }
      
      onSuccess();
    } catch (error: any) {
      console.error('Failed to save role:', error);
      alert(error.response?.data?.detail || 'Failed to save role');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-semibold">
            {role ? 'Edit Role' : 'Create Role'}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="flex-1 overflow-y-auto">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Role Name
              </label>
              <input
                type="text"
                {...register('name')}
                className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {errors.name && (
                <p className="mt-1 text-sm text-red-600">{errors.name.message}</p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Description
              </label>
              <textarea
                {...register('description')}
                rows={3}
                className="w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-3">
                Permissions
              </label>
              <div className="space-y-4">
                {availablePermissions.map((group) => {
                  const modulePermissions = group.permissions.map(
                    (p) => `${group.module}.${p}`
                  );
                  const allSelected = modulePermissions.every((p) =>
                    selectedPermissions.includes(p)
                  );
                  const someSelected = modulePermissions.some((p) =>
                    selectedPermissions.includes(p)
                  );

                  return (
                    <div key={group.module} className="border rounded-lg p-4">
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="font-medium text-gray-900 capitalize">
                          {group.module}
                        </h4>
                        <button
                          type="button"
                          onClick={() => toggleModule(group.module, group.permissions)}
                          className={`text-sm px-3 py-1 rounded ${
                            allSelected
                              ? 'bg-blue-100 text-blue-700'
                              : someSelected
                              ? 'bg-gray-100 text-gray-700'
                              : 'bg-gray-50 text-gray-600'
                          } hover:bg-blue-200`}
                        >
                          {allSelected ? 'Deselect All' : 'Select All'}
                        </button>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                        {group.permissions.map((permission) => {
                          const fullPermission = `${group.module}.${permission}`;
                          const isSelected = selectedPermissions.includes(fullPermission);

                          return (
                            <label
                              key={permission}
                              className="flex items-center space-x-2 cursor-pointer"
                            >
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => togglePermission(fullPermission)}
                                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                              />
                              <span className="text-sm text-gray-700">{permission}</span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="flex justify-between items-center mt-6 pt-4 border-t">
            <div className="text-sm text-gray-600">
              {selectedPermissions.length} permission{selectedPermissions.length !== 1 ? 's' : ''} selected
            </div>
            <div className="space-x-3">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-gray-700 bg-gray-200 rounded-lg hover:bg-gray-300"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isLoading}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {isLoading ? 'Saving...' : role ? 'Update' : 'Create'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
} 