'use client';

import { useAuth } from '@/contexts/AuthContext';
import { Users, Building2, BookOpen, DollarSign, Wrench, ArrowUpDown, PieChart } from 'lucide-react';
import Link from 'next/link';

export default function DashboardPage() {
  const { user } = useAuth();

  const stats = [
    { name: 'Total Users', value: '0', icon: Users, color: 'bg-blue-500' },
    { name: 'Companies', value: '1', icon: Building2, color: 'bg-green-500' },
    { name: 'GL Accounts', value: '0', icon: BookOpen, color: 'bg-purple-500' },
    { name: 'Outstanding AR', value: '$0.00', icon: DollarSign, color: 'bg-yellow-500' },
  ];

  const quickActions = [
    {
      title: 'Maintenance',
      description: 'Manage master data and system setup',
      icon: Wrench,
      href: '/maintenance',
      color: 'bg-blue-500'
    },
    {
      title: 'Transactions',
      description: 'Process daily operational transactions',
      icon: ArrowUpDown,
      href: '/transactions',
      color: 'bg-green-500'
    },
    {
      title: 'Reports',
      description: 'Generate business reports and analytics',
      icon: PieChart,
      href: '/reports',
      color: 'bg-purple-500'
    },
  ];

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">
          Welcome back, {user?.first_name || user?.username}!
        </h1>
        <p className="text-gray-600 mt-2">
          Here's what's happening with your business today.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div key={stat.name} className="bg-white p-6 rounded-lg shadow">
              <div className="flex items-center">
                <div className={`${stat.color} p-3 rounded-lg`}>
                  <Icon className="h-6 w-6 text-white" />
                </div>
                <div className="ml-4">
                  <p className="text-sm text-gray-600">{stat.name}</p>
                  <p className="text-2xl font-semibold text-gray-900">
                    {stat.value}
                  </p>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Quick Actions for Three-Tier Navigation */}
      <div className="mb-8">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Quick Actions</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {quickActions.map((action) => {
            const Icon = action.icon;
            return (
              <Link key={action.title} href={action.href}>
                <div className="bg-white p-6 rounded-lg shadow hover:shadow-lg transition-shadow cursor-pointer">
                  <div className="flex items-center mb-4">
                    <div className={`${action.color} p-3 rounded-lg`}>
                      <Icon className="h-8 w-8 text-white" />
                    </div>
                    <h3 className="text-lg font-semibold text-gray-900 ml-4">
                      {action.title}
                    </h3>
                  </div>
                  <p className="text-gray-600">{action.description}</p>
                </div>
              </Link>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-4">Recent Activities</h2>
          <p className="text-gray-600">No recent activities to display.</p>
        </div>
        
        <div className="bg-white p-6 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
          <div className="space-y-2">
            <button className="w-full text-left px-4 py-2 rounded hover:bg-gray-50">
              Create New Invoice
            </button>
            <button className="w-full text-left px-4 py-2 rounded hover:bg-gray-50">
              Add New Customer
            </button>
            <button className="w-full text-left px-4 py-2 rounded hover:bg-gray-50">
              View Reports
            </button>
          </div>
        </div>
      </div>
    </div>
  );
} 