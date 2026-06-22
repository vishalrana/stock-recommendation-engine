import React from 'react';

export default function Loading() {
  return (
    <main className="min-h-screen bg-gray-50 py-12 px-4 sm:px-6 lg:px-8 animate-pulse">
      <div className="max-w-7xl mx-auto">
        <header className="mb-10 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <div className="h-8 bg-gray-200 rounded w-64 mb-3"></div>
            <div className="h-4 bg-gray-200 rounded w-96"></div>
          </div>
          <div className="h-10 bg-gray-200 rounded w-44"></div>
        </header>

        <div className="bg-white rounded-lg shadow border border-gray-200 p-6">
          <div className="h-9 bg-gray-200 rounded w-80 mb-6"></div>
          
          <div className="space-y-4">
            <div className="h-10 bg-gray-100 rounded w-full"></div>
            <div className="h-12 bg-gray-50 rounded w-full"></div>
            <div className="h-12 bg-gray-50 rounded w-full"></div>
            <div className="h-12 bg-gray-50 rounded w-full"></div>
            <div className="h-12 bg-gray-50 rounded w-full"></div>
            <div className="h-12 bg-gray-50 rounded w-full"></div>
          </div>
        </div>
      </div>
    </main>
  );
}
